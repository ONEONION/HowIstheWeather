#!/usr/bin/env python3

import time
import requests
import os
import logging
import numpy as np
from moviepy.editor import ImageSequenceClip
import imageio
from PIL import ImageDraw, Image
logger = logging.getLogger('log')


#try:
#    from wxcloudrun.site_packages.moviepy.ImageSequenceClip import ImageSequenceClip
#except Exception as e:
#    logger.info(e.with_traceback)
# try:
#    from wxcloudrun.site_packages.PIL import ImageDraw, Image
#except Exception as e:
#    logger.info(e.with_traceback)


# 微信云托管
UploadUrl = 'https://api.weixin.qq.com/cgi-bin/media/upload?access_token={}&type={}'
AccessTokenUrl = 'https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={}&secret={}'
AccessToken = None
# 高德地图
MapKey = '248f4fad0db78a6c07ad45fc041775f6'
MapUrl = 'https://restapi.amap.com/v3/staticmap?location={},{}&zoom={}&key={}&size=461*461'
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
    
    map_img = get_map(location_x, location_y, scale)
    radar_imgs = get_radar(location_x, location_y)
    try:
        file_path = images2video(map_img, radar_imgs)
        file_type = 'video'
    except Exception as e:
        file_path = map_img
        file_type = 'image'
        logger.info(e.with_traceback)
    res = upload_file(file_path, file_type)
    if res[0] == 1:
        return [file_type, 'MediaId', res[1]]
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
    
    with open(MapSavePath+'map_img.png', 'wb') as img:
        img.write(map_img)

    return MapSavePath+'map_img.png'  


def get_access_token():
    global AccessToken
    if AccessToken is None or AccessToken['expire_time'] < time.time():
        rsp = requests.get(AccessTokenUrl.format(os.environ.get("APP_ID"), os.environ.get("APP_SECRET")))
    #    rsp = requests.get(AccessTokenUrl.format('wx6bb828210f8d7989', '8ac2dc63edc3a2cf6420508fbf6fe6de'))
        AccessToken = {'access_token': rsp.json()['access_token'], 
                    'expire_time': time.time() + rsp.json()['expires_in'] }
        logger.info('get access_token')
    return AccessToken['access_token']


def upload_file(file_url, file_type):
    # 上传图片/视频作为临时素材到微信服务器
    file = {
        'media': open(file_url, 'rb'),
    }
    try:
        rsp = requests.post(UploadUrl.format(get_access_token(), file_type), files = file)
        logger.info('post request: '+ UploadUrl.format(get_access_token(), file_type))
    except Exception as e:
        logger.info(e.with_traceback)
        return -1, '上传素材失败'
    try:
        return 1, rsp.json()['media_id']
    except Exception as e:
        logger.info(rsp.json())
        return -1, '上传素材失败'
    

GetTicketUrl = 'https://h5.caiyunapp.com/api/ticket'
GetRadarUrl = 'https://h5.caiyunapp.com/api/?ticket={}'

def get_radar(location_x, location_y):
    # 获取ticket
    rsp = requests.post(GetTicketUrl, headers={'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36'})    
    if rsp.status_code == 200:
        ticket = rsp.json()['ticket']
    else: 
        return rsp.json()

    # 获取雷达图地址
    rsp = requests.post(GetRadarUrl.format(ticket), 
                        headers={'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36'},
                        data = {'url': "https://api.caiyunapp.com/v1/radar/forecast_images?lon={}&lat={}&level=1&token=<t1>".format(location_y, location_x)})
    if rsp.status_code == 200 and rsp.json()['status'] == 'ok':
        images_url = rsp.json()['images']
    else:
        return rsp.json()
    
    radar_images = []
    # 批量下载雷达图
    for i in range(len(images_url)):
        rsp = requests.get(images_url[i][0], headers={'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36'})
        if rsp.status_code == 200:
            radar_images.append({'image': MapSavePath+'forecast_img_%d.png'%i, 
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(images_url[i][1]))
                        })
            with open(MapSavePath+'forecast_img_%d.png'%i, 'wb') as img:
                img.write(rsp.content)
    return radar_images


def images2video(background_img, images):
    # background_img是图片地址
    # images是元素为{'image':图片地址, 'timestamp': 时间戳}的数组
    # background = modify_alpha(np.asarray(Image.open(background_img)))
    background = modify_alpha(imageio.imread(background_img))

    frames= []
    for img in images:
        weather_img = Image.open(img['image'])
        draw = ImageDraw.Draw(weather_img)
        draw.text((340, 0), img['timestamp'], fill=(255, 255, 255))
        weather_img.save(img['image'])

        weather_img = np.asarray(weather_img)
        combined_img = (background + weather_img).astype(np.uint8)
        frames.append(combined_img)
        # frames.append(img['image'])

    # logger.info(len(frames))
    clip = ImageSequenceClip(frames, fps=12)
    clip.write_videofile(MapSavePath+'output_video.mp4')
    
    # raise KeyError
    
    return MapSavePath + 'output_video.mp4'


def modify_alpha(image):
    if image.shape[-1] == 4:
        # 如果原图有alpha通道
        for i in range(image.shape[0]):
            for j in range(image.shape[1]): 
                if image[i,j,0] + image[i,j,1] + image[i,j,2] != 0:
                    image[i,j,3] = 255
    elif image.shape[-1] == 3:
        # 如果原图没有alpha通道
        alpha = np.zeros((461, 461), dtype=np.uint8) * 255
        image = np.stack((image[:,:,0], image[:,:,1], image[:,:,2], alpha), axis = 2)
    else:
        raise Exception
    return image



if __name__ == '__main__':
    print('get weather')
    # print(get_weather(39.849968, 116.401463, 12))
    # media_id = upload_img(get_map(39.629968, 116.401463, 15))
    bg = get_map(42.1606, 126.2953, 12)
    radar_imgs = get_radar(42.1606, 126.2953)
    # radar_imgs = [{'image': './wxcloudrun/maps/forecast_img_0.png', 'timestamp': '2024-10-11 15:31:02'}, {'image': './wxcloudrun/maps/forecast_img_1.png', 'timestamp': '2024-10-11 15:36:49'}, {'image': './wxcloudrun/maps/forecast_img_2.png', 'timestamp': '2024-10-11 15:42:36'}, {'image': './wxcloudrun/maps/forecast_img_3.png', 'timestamp': '2024-10-11 15:48:23'}, {'image': './wxcloudrun/maps/forecast_img_4.png', 'timestamp': '2024-10-11 15:54:10'}, {'image': './wxcloudrun/maps/forecast_img_5.png', 'timestamp': '2024-10-11 15:59:57'}, {'image': './wxcloudrun/maps/forecast_img_6.png', 'timestamp': '2024-10-11 16:05:44'}, {'image': './wxcloudrun/maps/forecast_img_7.png', 'timestamp': '2024-10-11 16:11:31'}, {'image': './wxcloudrun/maps/forecast_img_8.png', 'timestamp': '2024-10-11 16:17:18'}, {'image': './wxcloudrun/maps/forecast_img_9.png', 'timestamp': '2024-10-11 16:23:05'}, {'image': './wxcloudrun/maps/forecast_img_10.png', 'timestamp': '2024-10-11 16:28:52'}, {'image': './wxcloudrun/maps/forecast_img_11.png', 'timestamp': '2024-10-11 16:34:39'}, {'image': './wxcloudrun/maps/forecast_img_12.png', 'timestamp': '2024-10-11 16:40:26'}, {'image': './wxcloudrun/maps/forecast_img_13.png', 'timestamp': '2024-10-11 16:46:13'}, {'image': './wxcloudrun/maps/forecast_img_14.png', 'timestamp': '2024-10-11 16:52:00'}, {'image': './wxcloudrun/maps/forecast_img_15.png', 'timestamp': '2024-10-11 16:57:47'}, {'image': './wxcloudrun/maps/forecast_img_16.png', 'timestamp': '2024-10-11 17:03:34'}, {'image': './wxcloudrun/maps/forecast_img_17.png', 'timestamp': '2024-10-11 17:09:21'}, {'image': './wxcloudrun/maps/forecast_img_18.png', 'timestamp': '2024-10-11 17:15:08'}, {'image': './wxcloudrun/maps/forecast_img_19.png', 'timestamp': '2024-10-11 17:20:55'}, {'image': './wxcloudrun/maps/forecast_img_20.png', 'timestamp': '2024-10-11 17:26:42'}, {'image': './wxcloudrun/maps/forecast_img_21.png', 'timestamp': '2024-10-11 17:32:29'}, {'image': './wxcloudrun/maps/forecast_img_22.png', 'timestamp': '2024-10-11 17:38:16'}, {'image': './wxcloudrun/maps/forecast_img_23.png', 'timestamp': '2024-10-11 17:44:03'}, {'image': './wxcloudrun/maps/forecast_img_24.png', 'timestamp': '2024-10-11 17:49:50'}, {'image': './wxcloudrun/maps/forecast_img_25.png', 'timestamp': '2024-10-11 17:55:37'}]
    # bg = MapSavePath + 'map_img.png'
    print(images2video(bg, radar_imgs))
    