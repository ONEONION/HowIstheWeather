from datetime import datetime

from django.db import models


# Create your models here.
class Counters(models.Model):
    id = models.AutoField
    count = models.IntegerField(max_length=11, default=0)
    createdAt = models.DateTimeField(default=datetime.now(), )
    updatedAt = models.DateTimeField(default=datetime.now(),)

    def __str__(self):
        return self.title

    class Meta:
        db_table = 'Counters'  # 数据库表名


class RequestHistory(models.Model):
    id = models.AutoField(primary_key=True)
    requestUser = models.CharField(default='noName', max_length=128)
    location = models.TextField(default='北京市海淀区西土城路')
    createdAt = models.DateTimeField(default=datetime.now(),)

    def __str__(self):
        return self.title

    class Meta:
        db_table = 'RequestHistory'