from django.urls import path
from . import views

urlpatterns = [
    path('requirement/', views.RequirementListView.as_view(), name='requirement-list'),
    path('requirement/status/<str:status>', views.RequirementListStatusView.as_view(), name='requirement-status-list'),
    path('requirement/new', views.RequirementCreateView.as_view(), name='new-requirement'),
    path('requirement/<pk>/edit', views.RequirementUpdateView.as_view(), name='edit-requirement'),
    path('requirement/<pk>/delete', views.RequirementDeleteView.as_view(), name='delete-requirement'),
    path('requirement/<pk>', views.RequirementView.as_view(), name='requirement'),
    path('requirement/<int:pk>/update-status/<str:status>/', views.RequirementStatusUpdateView.as_view(), name='requirement-update-status'),
    path('requirement/<int:pk>/decline/', views.RFQDeclineView.as_view(), name='requirement-decline'),
    path('requirement/<int:pk>/questions/', views.RequirementQuestionListCreateView.as_view(), name='requirement-questions'),
    path('requirement/questions/<int:pk>/answer/', views.RequirementQuestionAnswerView.as_view(), name='requirement-question-answer'),

    path('quote/', views.QuoteListView.as_view(), name='quote-list'),
    path('quote/new/<pk>', views.QuoteCreateView.as_view(), name='new-quote'),
    path('quote/<pk>/edit', views.QuoteUpdateView.as_view(), name='edit-quote'),
    path('quote/<pk>/delete', views.QuoteDeleteView.as_view(), name='delete-quote'),
    path('quote/<pk>', views.QuoteView.as_view(), name='quote'),
    path('quote/<int:pk>/update-status/<str:status>/', views.QuoteStatusUpdateView.as_view(), name='quote-update-status'),

    path('orders/', views.OrderListView.as_view(), name='orders-list'),
    path('orders/<billno>', views.OrderDetailView.as_view(), name='order-detail'),
    path('orders/<billno>/update-status/<str:status>/', views.OrderStatusUpdateView.as_view(), name='order-update-status'),
    path('orders/<billno>/production/advance/', views.OrderProductionAdvanceView.as_view(), name='order-production-advance'),
    path('orders/<billno>/updates/', views.OrderUpdateCreateView.as_view(), name='order-post-update'),
    path('orders/<billno>/qc/<int:item_pk>/toggle/', views.QCChecklistToggleView.as_view(), name='order-qc-toggle'),
    path('orders/<billno>/shipment/', views.OrderShipmentUpdateView.as_view(), name='order-shipment-update'),

    path('search/', views.global_search_view.as_view(), name='global_search_view'),
]
