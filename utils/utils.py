def demand_buttons(demand, is_staff=0):
    context = {}
    buttons = []
    if demand.status == 'Approved':
        if is_staff:
            buttons.append({'url': 'demand-update-status', 'pk': demand.pk, 'status': 'Production', 'label': '+ Start Production', 'class': 'btn ghost-green', 'style': 'float:right;'})
        else:
            buttons.append({'label': 'Approved', 'class': 'btn ghost-green', 'style': 'float:right;'})
    if demand.status == 'Production':
        if is_staff:
            buttons.append({'url': 'demand-update-status', 'pk': demand.pk, 'status': 'Completed', 'label': '+ Complete RFQ', 'class': 'btn ghost-green', 'style': 'float:right;'})
        else:
            buttons.append({'label': 'In Production', 'class': 'btn ghost-green', 'style': 'float:right;'})
    if demand.status == 'Completed':
        if is_staff:
            buttons.append({'url': 'demand-update-status', 'pk': demand.pk, 'status': 'Completed', 'label': '+ Generate Bill', 'class': 'btn ghost-green', 'style': 'float:right;'})
        else:
            buttons.append({'label': 'Completed', 'class': 'btn ghost-green', 'style': 'float:right;'})
    return buttons
