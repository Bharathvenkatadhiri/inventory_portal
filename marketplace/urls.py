from django.contrib.auth.decorators import login_not_required
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
    path('requirement/<int:pk>/extend/', views.RequirementExtendView.as_view(), name='requirement-extend'),
    path('changes/<int:pk>/withdraw/', views.AmendmentWithdrawView.as_view(), name='amendment-withdraw'),
    path('changes/response/<int:pk>/respond/', views.AmendmentRespondView.as_view(), name='amendment-respond'),
    path('changes/response/<int:pk>/decide/', views.AmendmentDecisionView.as_view(), name='amendment-decide'),

    path('quote/', views.QuoteListView.as_view(), name='quote-list'),
    path('quote/new/<pk>', views.QuoteCreateView.as_view(), name='new-quote'),
    path('quote/<pk>/edit', views.QuoteUpdateView.as_view(), name='edit-quote'),
    path('quote/<pk>/delete', views.QuoteDeleteView.as_view(), name='delete-quote'),
    path('quote/<pk>', views.QuoteView.as_view(), name='quote'),
    path('quote/<int:pk>/update-status/<str:status>/', views.QuoteStatusUpdateView.as_view(), name='quote-update-status'),
    path('quote/<int:pk>/request-revision/', views.QuoteRevisionRequestView.as_view(), name='quote-request-revision'),
    path('quote/<int:pk>/decline-revision/', views.QuoteRevisionDeclineView.as_view(), name='quote-decline-revision'),

    path('reports/', views.ReportsView.as_view(), name='reports'),
    path('orders/', views.OrderListView.as_view(), name='orders-list'),
    path('orders/<billno>', views.OrderDetailView.as_view(), name='order-detail'),
    path('orders/<billno>/update-status/<str:status>/', views.OrderStatusUpdateView.as_view(), name='order-update-status'),
    path('orders/<billno>/production/advance/', views.OrderProductionAdvanceView.as_view(), name='order-production-advance'),
    path('orders/<billno>/updates/', views.OrderUpdateCreateView.as_view(), name='order-post-update'),
    path('orders/<billno>/qc/<int:index>/toggle/', views.QCChecklistToggleView.as_view(), name='order-qc-toggle'),
    path('orders/<billno>/review/', views.OrderReviewCreateView.as_view(), name='order-review'),
    path('orders/<billno>/shipment/', views.OrderShipmentUpdateView.as_view(), name='order-shipment-update'),

    path('search/', views.global_search_view.as_view(), name='global_search_view'),

    path('documents/', views.DocumentListView.as_view(), name='document-list'),
    path('notifications/', views.NotificationListView.as_view(), name='notification-list'),
    path('notifications/open/', views.NotificationOpenView.as_view(), name='notification-open'),
    path('notifications/read/', views.NotificationMarkReadView.as_view(), name='notification-mark-read'),
    path('notifications/read-all/', views.NotificationMarkAllReadView.as_view(), name='notification-mark-all-read'),

    path('messages/', views.MessageThreadListView.as_view(), name='message-thread-list'),
    path('messages/start/<int:requirement_pk>/<int:supplier_pk>/', views.MessageThreadStartView.as_view(), name='message-thread-start'),
    path('messages/ask-buyer/<int:requirement_pk>/', views.MessageThreadStartView.as_view(), name='message-thread-ask-buyer'),
    path('messages/<int:pk>/', views.MessageThreadDetailView.as_view(), name='message-thread'),
    path('messages/<int:pk>/close/', views.MessageThreadCloseView.as_view(), name='message-thread-close'),
    path('messages/message/<int:pk>/delete/', views.MessageDeleteView.as_view(), name='message-delete'),
    path('messages/message/<int:pk>/attachment/', views.MessageAttachmentDownloadView.as_view(), name='message-attachment'),

    # Live-update pollers. Exempt from the login redirect on purpose: they
    # answer 286 ("stop polling") when the session has ended.
    path('live/topbar/', login_not_required(views.LiveTopbarView.as_view()), name='live-topbar'),
    path('live/messages/<int:pk>/', login_not_required(views.MessageThreadPollView.as_view()), name='live-thread'),
    path('live/quotes/<int:pk>/', login_not_required(views.QuotesSectionPollView.as_view()), name='live-quotes'),
]
