from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [('my_app', '0026_sportsground')]
    operations = [migrations.CreateModel(name='PaymentTransaction', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
        ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
        ('provider', models.CharField(default='razorpay', max_length=32)), ('provider_order_id', models.CharField(db_index=True, max_length=64, unique=True)),
        ('provider_payment_id', models.CharField(blank=True, max_length=64, null=True, unique=True)), ('amount_paise', models.PositiveIntegerField()),
        ('currency', models.CharField(default='INR', max_length=3)), ('status', models.CharField(choices=[('created', 'Created'), ('authorized', 'Authorized'), ('captured', 'Captured'), ('failed', 'Failed')], db_index=True, default='created', max_length=16)),
        ('captured_at', models.DateTimeField(blank=True, null=True)), ('failure_reason', models.CharField(blank=True, max_length=255)),
        ('booking', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='payment', to='my_app.courtbooking')),
    ]), migrations.AddIndex(model_name='paymenttransaction', index=models.Index(fields=['status', 'created_at'], name='payment_status_created_idx'))]
