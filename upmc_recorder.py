#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import datetime
import glob
import json
import logging
import os
import re
import requests
import shutil
from cm_client import CampusManagerClient

session = None


def make_request(url, method='get', headers=None, params=None, data=None, files=None, verify=False, proxies=None, json=False, timeout=120):
    global session
    if session is None:
        session = requests.Session()

    if method == 'get':
        req_function = session.get
    else:
        req_function = session.post
    resp = req_function(
        url=url,
        headers=headers,
        params=params,
        data=data,
        files=files,
        timeout=timeout,
        proxies=proxies,
        verify=verify,
    )
    if resp.status_code != 200:
        raise Exception('HTTP %s error on %s', resp.status_code, url)

    return resp.json() if json else resp.text.strip()


class UPMCRecorder(CampusManagerClient):
    '''
    UPMC recording script to handle Monarch, Campus Manager and MediaServer interactions
    '''
    DEFAULT_CONF = {
        'CAPABILITIES': {  # This list makes available or not actions buttons in Campus Manager
            'recording': {},
        },
        'MS_BASE_URL': 'https://mediaserver.captation.upmc.fr/api/v2/',
        'MS_API_KEY': '',
        'MONARCH_LOGIN': 'admin',
        'MONARCH_PASSWORD': 'admin',
        'MONARCH_IP': 'X.X.X.X',
        'RECORDER_NAME': 'roomX',
        'RECORDER_LOCATION': 'Room X',
        'RECORDER_FILE': 'captation-roomX',
        'RECORDER_NAS_PWD': 'roomX/',  # This should be a path on the NAS
    }

    HOME_DIR = '/home/omnictrl/'
    MONARCH_STATUS_RE = re.compile(r'RECORD:(?P<record_state>[A-Z]+),STREAM:(?P<stream_mode>[A-Z]+),(?P<stream_state>[A-Z]+),NAME:(?P<device_name>.+)$', re.IGNORECASE)
    VIDEO_FILE = 'video_original.mp4'
    PROFILES = {'omni': {
        'has_password': False,
        'can_live': True,
        'name': 'omni',
        'label': 'Omnilive',
        'type': 'recorder',
    }}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.temp_params = None
        self.wait_params = None
        self.metadata = None
        try:
            self.update_capabilities()
        except Exception:
            pass  # do not block the client

    def handle_action(self, action, params):
        if action == 'SHUTDOWN':
            logger.info('Shutdown requested.')
            # return json.dumps(info)
        elif action == 'REBOOT':
            logger.info('Reboot requested.')
            # return json.dumps(info)
        elif action == 'START_RECORDING':
            logger.info('Starting recording with params %s', params)
            self.set_status(status='initializing', remaining_space='auto')
            result = self.omnibox_request('GetStatus')
            s = re.search(self.MONARCH_STATUS_RE, result)
            if s.group('record_state') == 'DISABLED':
                logger.error('L\'omnibox est arrêté ou l\'enregistrement n\'est pas activé')
            else:
                if s.group('record_state') == 'READY':
                    self.wait_params = ''
                    self.temp_params = params
                    if 'live' in params:
                        result = self.ms_streaming_control('PREPARE_STREAMING', params)
                        command = '%s,%s,%s' % ('SetRTMP', 'rtmp:/', result['publish_uri'][7:].replace(
                            'streaming-mserver.upmc.fr', '10.11.0.10'))
                        self.omnibox_request(command)
                        self.ms_streaming_control('START_STREAMING', params)
                        self.omnibox_request('StartStreamingAndRecording')
                    else:
                        self.omnibox_request('StartRecording')
                elif s.group('record_state') == 'ON':
                    self.wait_params = params
                    params = self.temp_params
                current_time = datetime.datetime.now()
                if 'title' not in params:
                    params['title'] = 'untitled'
                if 'speaker_id' not in params:
                    params['speaker_id'] = ''
                if 'course_id' not in params:
                    params['course_id'] = ''
                if 'speaker_email' not in params:
                    params['speaker_email'] = ''
                if 'profile' not in params:
                    params['profile'] = ''
                if 'speaker' not in params:
                    params['speaker'] = ''
                self.metadata = {
                    'title': params['title'],
                    'creation_date': current_time.strftime('%Y-%m-%d %H:%M:%S'),
                    'location': self.LOCATION,
                    'language': 'fr',
                    'layout': 'composition_4',
                    'code': current_time.strftime('%Y-%m-%d_%H-%M-%S') + '_%s' % self.conf['RECORDER_NAME'],
                    'speaker_id': params['speaker_id'],
                    'speaker_name': params['speaker'],
                    'speaker_email': params['speaker_email'],
                    'course_id': params['course_id'],
                    'profile': params['profile'],
                    'unlisted': 'no',
                }
            self.set_status(status='recording')
        elif action == 'STOP_RECORDING':
            logger.info('Stopping recording.')
            self.set_status(status='ready', remaining_space='auto')
            result = self.omnibox_request('GetStatus')
            s = re.search(self.MONARCH_STATUS_RE, result)
            if s.group('record_state') == 'ON':
                if not self.metadata:
                    self.metadata = {
                        'title': 'untitled',
                        'creation_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'location': self.LOCATION,
                        'language': 'fr',
                        'layout': 'composition_4',
                    }
                self.ms_streaming_control('STOP_STREAMING', params)
                self.omnibox_request('StopStreamingAndRecording')
                self.ms_video_upload(self.FILE + '-[*.mp4', self.metadata)
                if 'title' in self.wait_params:
                    if 'live' in self.wait_params:
                        result = self.ms_streaming_control('PREPARE_STREAMING', self.wait_params)
                        command = '%s,%s,%s' % ('SetRTMP', 'rtmp:/', result['publish_uri'][7:].replace('streaming-mserver.upmc.fr', '10.11.0.10'))
                        self.omnibox_request(command)
                        self.ms_streaming_control('START_STREAMING', self.wait_params)
                        self.omnibox_request('StartStreamingAndRecording')
                    else:
                        self.omnibox_request('StartRecording')
                    current_time = datetime.datetime.now()
                    if 'title' not in params:
                        params['title'] = 'untitled'
                    if 'speaker_id' not in params:
                        params['speaker_id'] = ''
                    if 'course_id' not in params:
                        params['course_id'] = ''
                    if 'speaker_email' not in params:
                        params['speaker_email'] = ''
                    if 'profile' not in params:
                        params['profile'] = ''
                    if 'speaker' not in params:
                        params['speaker'] = ''
                    self.metadata = {
                        'title': self.wait_params['title'],
                        'creation_date': current_time.strftime('%Y-%m-%d %H:%M:%S'),
                        'location': self.LOCATION,
                        'language': 'fr',
                        'layout': 'composition_4',
                        'code': '',
                        'speaker_id': params['speaker_id'],
                        'speaker_name': params['speaker'],
                        'speaker_email': params['speaker_email'],
                        'course_id': params['course_id'],
                        'profile': params['profile'],
                        'unlisted': 'no',
                    }
                    self.wait_params = ''
            else:
                logger.error('L\'enregistrement n\'était pas démarré')
        elif action == 'LIST_PROFILES':
            logger.info('Updating capabilities.')
            self.update_capabilities()
            logger.info('Returning list of profiles.')
            return json.dumps(self.PROFILES)
        else:
            raise Exception('Unrecognized action.')

    def omnibox_request(self, command):
        url = 'http://' + self.conf['MONARCH_LOGIN'] + ':' + self.conf['MONARCH_PASSWORD'] + '@' + self.conf['MONARCH_IP'] + '/Monarch/syncconnect/sdk.aspx?command=' + command
        return make_request(url, proxies={'http': '', 'https': ''})

    def ms_api_request(self, suffix, method='get', **kwargs):
        url = requests.compat.urljoin(self.conf['MS_BASE_URL'], suffix)
        if method == 'get':
            if not kwargs.get('params'):
                kwargs['params'] = dict()
            kwargs['params']['api_key'] = self.conf['MS_API_KEY']
        else:
            if not kwargs.get('data'):
                kwargs['data'] = dict()
            kwargs['data']['api_key'] = self.conf['MS_API_KEY']
        kwargs['json'] = True
        kwargs['proxies'] = {'http': '', 'https': ''}
        return make_request(url, method=method, **kwargs)

    def ms_streaming_control(self, action, params):
        logger.info('Running action %s on streaming.', action)
        if action == 'PREPARE_STREAMING':
            result = self.ms_api_request('lives/prepare/', method='post', data={
                'title': 'Live %s' % self.conf['RECORDER_LOCATION'],
                'width': '1280',
                'height': '720',
                'layout': 'composition_4',
            })
        elif action == 'START_STREAMING':
            result = self.ms_api_request('lives/start/', method='post', data={'title': 'Live %s' % self.conf['RECORDER_LOCATION']})
        elif action == 'STOP_STREAMING':
            result = self.ms_api_request('lives/stop/', method='post', data={'title': 'Live %s' % self.conf['RECORDER_LOCATION']})
        else:
            raise Exception('Unsupported action.')
        return result

    def ms_video_upload(self, video_fname, metadata):
        video_pwd = '/home/msuser/msinstance/media/resources/'
        date_now = datetime.datetime.now()
        code = date_now.strftime('%Y-%m-%d_%H-%M-%S') + '_%s' % self.conf['RECORDER_NAME']
        rep_pwd = video_pwd + code + '/'
        if not os.path.exists(rep_pwd):
            os.makedirs(rep_pwd)
            os.chown(rep_pwd, 118, 125)
        if video_fname != self.VIDEO_FILE:
            for file in glob.glob(self.HOME_DIR + self.conf['RECORDER_NAS_PWD'] + video_fname):
                os.chown(file, 118, 125)
                shutil.move(file, rep_pwd + self.VIDEO_FILE)

        logger.info('Uploading %s', video_fname)
        data = dict(metadata)
        data['code'] = code
        result = self.ms_api_request('medias/add/', method='post', params=data, data=data, timeout=300)
        if result['success'] == 'false':
            logger.error(result['error'])


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser(description=UPMCRecorder.__doc__.strip())
    parser.add_argument('name', help='Client name, for example: "amphi15".')
    args = parser.parse_args()
    # start client
    logger = logging.getLogger('recorder_%s' % args.name)
    UPMCRecorder.LOCAL_CONF = UPMCRecorder.HOME_DIR + '.cm_upmc_%s.json' % args.name
    if not os.path.exists(UPMCRecorder.LOCAL_CONF):
        raise Exception('The file "%s" does not exists.' % UPMCRecorder.LOCAL_CONF)
    client = UPMCRecorder()
    try:
        client.long_polling_loop()
    except KeyboardInterrupt:
        logger.info('KeyboardInterrupt received, stopping application.')
