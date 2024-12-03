import json
import logging
import time

from django.http import JsonResponse
from django.shortcuts import render
from wxcloudrun.models import Counters, RequestHistory
from wxcloudrun.weather import get_weather


logger = logging.getLogger('log')


def index(request, _):
    """
    获取主页

     `` request `` 请求对象
    """

    return render(request, 'index.html')


def counter(request, _):
    """
    获取当前计数

     `` request `` 请求对象
    """

    rsp = JsonResponse({'code': 0, 'errorMsg': ''}, json_dumps_params={'ensure_ascii': False})
    if request.method == 'GET' or request.method == 'get':
        rsp = get_count()
    elif request.method == 'POST' or request.method == 'post':
        rsp = update_count(request)
    else:
        rsp = JsonResponse({'code': -1, 'errorMsg': '请求方式错误'},
                            json_dumps_params={'ensure_ascii': False})
    logger.info('response result: {}'.format(rsp.content.decode('utf-8')))
    return rsp


def get_count():
    """
    获取当前计数
    """

    try:
        data = Counters.objects.get(id=1)
    except Counters.DoesNotExist:
        return JsonResponse({'code': 0, 'data': 0},
                    json_dumps_params={'ensure_ascii': False})
    return JsonResponse({'code': 0, 'data': data.count},
                        json_dumps_params={'ensure_ascii': False})


def update_count(request):
    """
    更新计数，自增或者清零

    `` request `` 请求对象
    """

    logger.info('update_count req: {}'.format(request.body))

    body_unicode = request.body.decode('utf-8')
    body = json.loads(body_unicode)

    if 'action' not in body:
        return JsonResponse({'code': -1, 'errorMsg': '缺少action参数'},
                            json_dumps_params={'ensure_ascii': False})

    if body['action'] == 'inc':
        try:
            data = Counters.objects.get(id=1)
        except Counters.DoesNotExist:
            data = Counters()
        data.id = 1
        data.count += 1
        data.save()
        return JsonResponse({'code': 0, "data": data.count},
                    json_dumps_params={'ensure_ascii': False})
    elif body['action'] == 'clear':
        try:
            data = Counters.objects.get(id=1)
            data.delete()
        except Counters.DoesNotExist:
            logger.info('record not exist')
        return JsonResponse({'code': 0, 'data': 0},
                    json_dumps_params={'ensure_ascii': False})
    else:
        return JsonResponse({'code': -1, 'errorMsg': 'action参数错误'},
                    json_dumps_params={'ensure_ascii': False})

def weather(request, _):
    """
    获取地址对应的天气
    """
    
    body_unicode = request.body.decode('utf-8')
    body = json.loads(body_unicode)
    logger.info('query weather req:' + json.dumps(body))

    if 'ToUserName' not in body:
        return JsonResponse({'code': -1, 'errorMsg': '参数不全'},
                            json_dumps_params={'ensure_ascii': False})
    
        
    
    rspContent = {'ToUserName': body['FromUserName'],
                        'FromUserName': body['ToUserName'],
                        'CreateTime': int(time.time()),
                        'MsgType': 'text',
    }
    data = RequestHistory(requestUser=body['FromUserName'], 
                        msgType = body['MsgType'],)

    if body['MsgType'] == 'text':
        rspContent['Content'] = '收到你的消息了，你想' + body['Content'] + '，直接发定位给我可以查两小时内的天气~'
        data.content = body['Content']
    elif body['MsgType'] == 'location':
        res = get_weather(body['Location_X'], body['Location_Y'], body['Scale'])
        rspContent['MsgType'] = res[0]
        if res[0] == 'text':
            rspContent[res[1]] = res[2]
        else:
            rspContent[res[0]] = {
                res[1]: res[2],
            }
        data.content = '{},{},{}'.format(body['Location_X'], body['Location_Y'], body['Label'])
    else:
        rspContent['Content'] = '暂时不懂你想做什么哦，直接发定位给我可以查两小时内的天气~'
        if body['MsgType'] in ('image', 'voice', 'video'):
            data.content = body['MediaId']


    data.save()
    logger.info('response result: {}'.format(rspContent))
    return JsonResponse(rspContent, json_dumps_params={'ensure_ascii': False})
