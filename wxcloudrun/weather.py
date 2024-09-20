#!/usr/bin/env python3

import time

import requests


HourlyWeatherUrl = 'https://api.caiyunapp.com/v2.6/kjkHlqwp1lHrU1RT/{},{}/hourly?hourlysteps=48'
MAX_RETRY = 3
err_code ={
    400: 'Token不存在',
    401: 'Token无权限',
    422: '参数错误',
    429: 'Token额度已用完', 
    500: '服务器错误',
}

def get_weather(location_x, location_y):
    data = {'status': 'failed',
                    'error': '天气API出错, 请重试',
                    'api_version': '2.6'}
    retry_times = 0

    while retry_times <= MAX_RETRY:
        try:
            rsp = requests.get(HourlyWeatherUrl.format(location_y, location_x))
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
                time.sleep(retry_times*retry_times)

        except Exception:
            retry_times += 1
            time.sleep(retry_times*retry_times)
            continue
    

    return data


if __name__ == '__main__':
    print('get weather')
    print(get_weather(39.914680, 116.359474))