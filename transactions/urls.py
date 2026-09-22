from django.urls import path
from django.conf.urls import url
from . import views

urlpatterns = [
    path('suppliers/', views.SupplierListView.as_view(), name='suppliers-list'),
    path('suppliers/<pk>/edit', views.SupplierUpdateView.as_view(), name='edit-supplier'),
    path('suppliers/<pk>/delete', views.SupplierDeleteView.as_view(), name='delete-supplier'),
    path('suppliers/<pk>/activate', views.SupplieractivateView.as_view(), name='activate-supplier'),
    path('supplier/<pk>', views.SupplierView.as_view(), name='supplier'),

    path('purchases/', views.PurchaseView.as_view(), name='purchases-list'), 
    path('purchases/new', views.SelectSupplierView.as_view(), name='select-supplier'), 
    path('purchases/new/<pk>', views.PurchaseCreateView.as_view(), name='new-purchase'),    
    path('purchases/<pk>/delete', views.PurchaseDeleteView.as_view(), name='delete-purchase'),
    
    path('sales/', views.SaleView.as_view(), name='sales-list'),
    path('sales/new', views.SaleCreateView.as_view(), name='new-sale'),
    path('sales/<pk>/delete', views.SaleDeleteView.as_view(), name='delete-sale'),

    path("purchases/<billno>", views.PurchaseBillView.as_view(), name="purchase-bill"),
    path("sales/<billno>", views.SaleBillView.as_view(), name="sale-bill"),

    path('demand/', views.DemandListView.as_view(), name='demand-list'),
    path('demand/status/<str:status>', views.DemandListStatusView.as_view(), name='demand-status-list'),
    path('demand/new', views.DemandCreateView.as_view(), name='new-demand'),
    path('demand/<pk>/edit', views.DemandUpdateView.as_view(), name='edit-demand'),
    path('demand/<pk>/delete', views.DemandDeleteView.as_view(), name='delete-demand'),
    path('demand/<pk>', views.DemandView.as_view(), name='demand'),


    path('quote/', views.QuoteListView.as_view(), name='quote-list'),
    path('quote/new/<pk>', views.QuoteCreateView.as_view(), name='new-quote'),
    path('quote/<pk>/edit', views.QuoteUpdateView.as_view(), name='edit-quote'),
    path('quote/<pk>/delete', views.QuoteDeleteView.as_view(), name='delete-quote'),
    path('quote/<pk>', views.QuoteView.as_view(), name='quote'),
    path('quote/<int:pk>/update-status/<str:status>/', views.QuoteStatusUpdateView.as_view(), name='quote-update-status'),
    path('demand/<int:pk>/update-status/<str:status>/', views.DemandStatusUpdateView.as_view(), name='demand-update-status'),

    path('search/', views.global_search_view.as_view(), name='global_search_view'),

]
