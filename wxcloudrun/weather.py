#!/usr/bin/env python3

import time
import requests
import os
import logging

logger = logging.getLogger('log')

# 微信云托管
UploadUrl = 'https://api.weixin.qq.com/cgi-bin/media/upload?access_token={}&type=image'
AccessTokenUrl = 'https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={}&secret={}'
AccessToken = None
# 高德地图
MapKey = '248f4fad0db78a6c07ad45fc041775f6'
MapUrl = 'https://restapi.amap.com/v3/staticmap?location={},{}&zoom={}&key={}&size=400*600'
MapSavePath = './wxcloudrun/maps/'
# 彩云天气
HourlyWeatherUrl = 'https://api.caiyunapp.com/v2.6/kjkHlqwp1lHrU1RT/{},{}/hourly'
MAX_RETRY = 3
err_code = {
    400: 'Token不存在',
    401: 'Token无权限',
    422: '参数错误',
    429: 'Token额度已用完', 
    500: '服务器错误',
}


def get_weather(location_x, location_y, scale):
    # 返回值格式为二者之一： 
    # ['image', 'MediaId', img_id]
    # ['text', 'Content', text]

    weather_data = query_weather(location_x, location_y)
    if weather_data['status'] != 'ok':
        return ['text', 'Content', weather_data['error']]
    
    map_img_path = get_map(location_x, location_y, scale)
    res = upload_img(map_img_path)
    if res[0] == 1:
        return ['image', 'MediaId', res[1]]
    else:
        return ['text', 'Content', res[1]]



def query_weather(location_x, location_y):
    data = {'status': 'failed',
            'error': '天气API出错, 请重试',
            'api_version': '2.6'}
    retry_times = 0

    while retry_times <= MAX_RETRY:
        try:
            rsp = requests.get(HourlyWeatherUrl.format(location_y, location_x))
            logger.info('get request:' + HourlyWeatherUrl.format(location_y, location_x))
            if rsp.status_code == 200: 
                data = rsp.json()
                break
            elif rsp.status_code in (400, 401, 422, 429):
                data = {'status': 'failed',
                        'error': '{}, 请联系管理员'.format(err_code[rsp.status_code]),
                        'api_version': '2.6',}
                break
            else:
                retry_times += 1

        except Exception:
            retry_times += 1
            time.sleep(retry_times*retry_times)
            continue
    return data


def get_map(location_x, location_y, scale):
    # 从高德地图获取图片背景
    data = {'status': 'failed',
            'error': '连接地图服务器出错, 请重试',
            'api_version': '2.6'}
    retry_times = 0
    while retry_times <= MAX_RETRY:
        try:
            rsp = requests.get(MapUrl.format(location_y, location_x, scale, MapKey))
            logger.info('request: '+ MapUrl.format(location_y, location_x, scale, MapKey))
            if rsp.status_code == 200: 
                map_img = rsp.content
                break
            else:
                retry_times += 1

        except Exception:
            retry_times += 1
            time.sleep(retry_times*retry_times)
            continue
    
    with open(MapSavePath+'map_img.jpg', 'wb') as img:
        img.write(map_img)

    return MapSavePath+'map_img.jpg'  


def get_access_token():
    global AccessToken
    if AccessToken is None or AccessToken['expire_time'] < time.time():
        rsp = requests.get(AccessTokenUrl.format(os.environ.get("APP_ID"), os.environ.get("APP_SECRET")))
    #    rsp = requests.get(AccessTokenUrl.format('wx6bb828210f8d7989', '8ac2dc63edc3a2cf6420508fbf6fe6de'))
        AccessToken = {'access_token': rsp.json()['access_token'], 
                    'expire_time': time.time() + rsp.json()['expires_in'] }
    logger.info('get access_token')
    return AccessToken['access_token']


def upload_img(img_url):
    img = {
        'media': open(img_url, 'rb'),

    }
    try:
        rsp = requests.post(UploadUrl.format(get_access_token()), files = img)
        logger.info('post request: '+ UploadUrl.format(get_access_token()))
    except Exception as e:
        logger.info(e.with_traceback)
        return -1, '上传图片失败'
    return 1, rsp.json()['media_id']
    

if __name__ == '__main__':
    print('get weather')
    # print(get_weather(39.849968, 116.401463, 12))
    media_id = upload_img(get_map(39.629968, 116.401463, 15))
    print(media_id)
    print(AccessToken)
    rsp = requests.get('https://api.weixin.qq.com/cgi-bin/media/get?access_token={}&media_id={}'.format(AccessToken['access_token'], media_id[1]))
    with open(MapSavePath + 'map_download.jpg', 'wb') as img:
        img.write(rsp.content)
    print(MapSavePath + 'map_download.jpg')
