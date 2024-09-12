from datetime import datetime

from django.db import models
from django.utils import timezone


# Create your models here.
class Counters(models.Model):
    id = models.AutoField
    count = models.IntegerField(default=0)
    createdAt = models.DateTimeField(default=timezone.now, )
    updatedAt = models.DateTimeField(default=timezone.now,)

    def __str__(self):
        return self.title

    class Meta:
        db_table = 'Counters'  # 数据库表名


class RequestHistory(models.Model):
    id = models.AutoField(primary_key=True)
    requestUser = models.CharField(default='dontKnowWho', max_length=128)
    msgType = models.CharField(default='text', max_length=32)
    content = models.TextField(default='北京市海淀区西土城路')
    createdAt = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.title

    class Meta:
        db_table = 'RequestHistory'