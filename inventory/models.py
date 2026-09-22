from django.db import models
    
class Stock(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=30, unique=True, verbose_name='Name')
    desc = models.CharField(max_length=200, blank=True, null=True)
    quantity = models.IntegerField(default=1)
    file = models.FileField(upload_to='stock_files/', blank=True, null=True)
    is_deleted = models.BooleanField(default=False)

    def __str__(self):
	    return self.name