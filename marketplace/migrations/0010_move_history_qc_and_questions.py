"""Moves data out of the tables being retired:

- OrderStatusHistory + ProductionStageHistory -> OrderEvent (kind status/stage)
- QCChecklistItem rows -> Order.qc_checklist
- RequirementQuestion -> a Message (and, if answered, a reply) in the
  buyer/supplier MessageThread for that RFQ

Original timestamps are kept, and NotificationRead keys pointing at the old
rows are rewritten so notifications a user already read stay read.
"""
from django.db import migrations

QC_CHECKLIST_ITEMS = [
    "First article inspection passed",
    "Dimensional report attached",
    "Material certification verified",
    "Visual / cosmetic inspection passed",
    "Packaging inspected",
]


def _rekey(NotificationRead, old_key, new_key):
    NotificationRead.objects.filter(key=old_key).update(key=new_key)


def forwards(apps, schema_editor):
    Order = apps.get_model('marketplace', 'Order')
    OrderEvent = apps.get_model('marketplace', 'OrderEvent')
    OrderStatusHistory = apps.get_model('marketplace', 'OrderStatusHistory')
    ProductionStageHistory = apps.get_model('marketplace', 'ProductionStageHistory')
    QCChecklistItem = apps.get_model('marketplace', 'QCChecklistItem')
    RequirementQuestion = apps.get_model('marketplace', 'RequirementQuestion')
    MessageThread = apps.get_model('marketplace', 'MessageThread')
    Message = apps.get_model('marketplace', 'Message')
    NotificationRead = apps.get_model('marketplace', 'NotificationRead')

    # 1. Order history -> OrderEvent
    for row in OrderStatusHistory.objects.all():
        event = OrderEvent.objects.create(
            order_id=row.order_id, kind='status', from_value=row.from_status or '',
            to_value=row.to_status, note=row.note or '',
        )
        OrderEvent.objects.filter(pk=event.pk).update(changed_at=row.changed_at)
        _rekey(NotificationRead, f'order-status:{row.pk}', f'order-event:{event.pk}')
    for row in ProductionStageHistory.objects.all():
        event = OrderEvent.objects.create(
            order_id=row.order_id, kind='stage', from_value=row.from_stage or '',
            to_value=row.to_stage, note=row.note or '',
        )
        OrderEvent.objects.filter(pk=event.pk).update(changed_at=row.changed_at)
        _rekey(NotificationRead, f'stage:{row.pk}', f'order-event:{event.pk}')

    # 2. QC checklist rows -> Order.qc_checklist
    for order in Order.objects.filter(pk__in=QCChecklistItem.objects.values('order_id')):
        items = list(QCChecklistItem.objects.filter(order_id=order.pk).select_related('checked_by'))
        items.sort(key=lambda i: QC_CHECKLIST_ITEMS.index(i.label) if i.label in QC_CHECKLIST_ITEMS else len(QC_CHECKLIST_ITEMS))
        order.qc_checklist = [{
            'label': item.label,
            'checked': item.is_checked,
            'checked_at': item.checked_at.isoformat() if item.checked_at else None,
            'checked_by': item.checked_by_id,
            'checked_by_name': (f"{item.checked_by.first_name} {item.checked_by.last_name}".strip() or item.checked_by.email) if item.checked_by else '',
        } for item in items]
        order.save(update_fields=['qc_checklist'])

    # 3. RFQ questions -> messages in that buyer/supplier thread
    for question in RequirementQuestion.objects.select_related('requirement', 'supplier').order_by('created_at'):
        buyer_id = question.requirement.user_id
        supplier_user_id = question.supplier.user_id
        thread, _ = MessageThread.objects.get_or_create(requirement_id=question.requirement_id, supplier_id=question.supplier_id)

        asked = Message.objects.create(thread=thread, sender_id=question.asked_by_id or supplier_user_id, body=question.question)
        Message.objects.filter(pk=asked.pk).update(created_at=question.created_at)
        _rekey(NotificationRead, f'question:{question.pk}', f'message:{asked.pk}')

        if question.answer:
            answered_at = question.answered_at or question.created_at
            reply = Message.objects.create(thread=thread, sender_id=buyer_id, body=question.answer)
            Message.objects.filter(pk=reply.pk).update(created_at=answered_at)
            # The buyer read the question when they answered it.
            if thread.buyer_last_read_at is None or thread.buyer_last_read_at < answered_at:
                thread.buyer_last_read_at = answered_at
            if NotificationRead.objects.filter(key=f'answer:{question.pk}', user_id=supplier_user_id).exists():
                if thread.supplier_last_read_at is None or thread.supplier_last_read_at < answered_at:
                    thread.supplier_last_read_at = answered_at
            thread.save(update_fields=['buyer_last_read_at', 'supplier_last_read_at'])
            _rekey(NotificationRead, f'answer:{question.pk}', f'message:{reply.pk}')


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0009_add_order_event_and_qc_checklist'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
