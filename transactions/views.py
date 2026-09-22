from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.generic import (
    View, 
    ListView,
    CreateView,
    UpdateView,
    DeleteView
)
from django.contrib.messages.views import SuccessMessageMixin
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from .models import (
    PurchaseBill, 
    Supplier, 
    PurchaseItem,
    PurchaseBillDetails,
    SaleBill,  
    SaleItem,
    SaleBillDetails,
    Customer,
    Demand,
    Quote,
    DemandParts,
    RfqBill
)
from accounts.models import (Supplier_details, Customer)
from django_filters.views import FilterView
from utils import utils


from .forms import (
    SelectSupplierForm, 
    PurchaseItemFormset,
    PurchaseDetailsForm, 
    SupplierForm, 
    SaleForm,
    SaleItemFormset,
    SaleDetailsForm,
    SelectDemand,
    SelectQuote,
    DemandPartsForm,
)

from accounts.forms import updateSupplierDetailsForm
from inventory.models import Stock
from django.db.models import Count
from django.views.generic.edit import CreateView, UpdateView
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import redirect, get_object_or_404
from .models import Supplier
from .forms import SupplierForm
from django.shortcuts import redirect
from django.views.generic.edit import CreateView
from django.forms import formset_factory
from django.utils import timezone
from django.conf import settings
from django.apps import apps

model_str = settings.AUTH_USER_MODEL
app_label, model_name = model_str.split('.')
User = apps.get_model(app_label, model_name)


class SupplierListView(ListView):
    model = Supplier_details
    template_name = "suppliers/suppliers_list.html"
    queryset = Supplier_details.objects.filter()
    paginate_by = 10

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return context    
class SupplierUpdateView(SuccessMessageMixin, UpdateView):
    model = Supplier_details
    form_class = updateSupplierDetailsForm
    success_url = '/transactions/suppliers'
    success_message = "Supplier details has been updated successfully"
    template_name = "suppliers/edit_supplier.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'Edit Supplier'
        context["savebtn"] = 'Save Changes'
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return context    


# used to delete a supplier
class SupplierDeleteView(View):
    template_name = "suppliers/delete_supplier.html"
    success_message = "Manufacturer has been deleted successfully"

    def get(self, request, pk):
        supplier = get_object_or_404(Supplier_details, pk=pk)
        base_template = 'customer_base.html'
        if self.request.user.is_staff:
            base_template = 'supplier_base.html'        
        return render(request, self.template_name, {'object' : supplier,'base_template':base_template})

    def post(self, request, pk):  
        supplier = get_object_or_404(Supplier_details, pk=pk)
        supplier.is_deleted = True
        supplier.save()                                               
        messages.success(request, self.success_message)
        return redirect('suppliers-list')
    
class SupplieractivateView(View):
    template_name = "suppliers/activate_supplier.html"
    success_message = "Supplier Record has been activated successfully"

    def get(self, request, pk):      
        supplier = get_object_or_404(Supplier_details, pk=pk)
        base_template = 'customer_base.html'
        if self.request.user.is_staff:
            base_template = 'supplier_base.html'        
        return render(request, self.template_name, {'object' : supplier,'base_template':base_template})

    def post(self, request, pk):
        user_id = Supplier_details.objects.filter(pk=pk).values('user_id').last()
        User.objects.filter(id=user_id['user_id']).update(is_active=True)
        supplier = get_object_or_404(Supplier_details, pk=pk)
        supplier.is_deleted = False
        supplier.save()
        messages.success(request, self.success_message)
        return redirect('suppliers-list')        

# used to view a supplier's profile
class SupplierView(View):
    def get(self, request, pk = ''):
        supplierobj = get_object_or_404(Supplier_details, pk=pk)
        paginate_by = 5
        context = {
            'supplier'  : supplierobj,
        }
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'        
        return render(request, 'suppliers/supplier.html', context)



class PurchaseView(ListView):
    model = PurchaseBill
    template_name = "purchases/purchases_list.html"
    context_object_name = 'bills'
    ordering = ['-time']
    paginate_by = 10

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return context    

# used to select the supplier
class SelectSupplierView(View):
    form_class = SelectSupplierForm
    template_name = 'purchases/select_supplier.html'

    def get(self, request, *args, **kwargs):                                    # loads the form page
        form = self.form_class
        base_template = 'customer_base.html'
        if self.request.user.is_staff:
            base_template = 'supplier_base.html'            
        return render(request, self.template_name, {'form': form,'base_template' : base_template})

    def post(self, request, *args, **kwargs):                                   # gets selected supplier and redirects to 'PurchaseCreateView' class
        form = self.form_class(request.POST)
        if form.is_valid():
            supplierid = request.POST.get("supplier")
            supplier = get_object_or_404(Supplier, id=supplierid)
            return redirect('new-purchase', supplier.pk)
        base_template = 'customer_base.html'
        if self.request.user.is_staff:
            base_template = 'supplier_base.html'            
        return render(request, self.template_name, {'form': form,'base_template' : base_template})

# used to generate a bill object and save items
class PurchaseCreateView(View):                                                 
    template_name = 'purchases/new_purchase.html'

    def get(self, request, pk):
        formset = PurchaseItemFormset(request.GET or None)                      # renders an empty formset
        supplierobj = get_object_or_404(Supplier, pk=pk)                        # gets the supplier object
        context = {
            'formset'   : formset,
            'supplier'  : supplierobj,
        }                                                                       # sends the supplier and formset as context
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'        
        return render(request, self.template_name, context)

    def post(self, request, pk):
        formset = PurchaseItemFormset(request.POST)                             # recieves a post method for the formset
        supplierobj = get_object_or_404(Supplier, pk=pk)                        # gets the supplier object
        if formset.is_valid():
            # saves bill
            billobj = formset.save(commit=False)
            billobj.save()

            billdetailsobj = SaleBillDetails(billno=billobj)
            billdetailsobj.save()


            for form in formset:                                                # for loop to save each individual form as its own object
                # false saves the item and links bill to the item
                billitem = form.save(commit=False)
                billitem.billno = billobj                                       # links the bill object to the items
                # gets the stock item
                stock = get_object_or_404(Stock, name=billitem.stock.name)       # gets the item
                # calculates the total price
                billitem.totalprice = billitem.perprice * billitem.quantity
                # updates quantity in stock db
                stock.quantity += billitem.quantity                             # updates quantity
                billdetailsobj.total += billitem.totalprice
                # saves bill item and stock
                stock.save()
                billitem.save()

            billdetailsobj.save()
            messages.success(request, "Purchased items have been registered successfully")
            return redirect('purchase-bill', billno=billobj.billno)
        formset = PurchaseItemFormset(request.GET or None)
        context = {
            'formset'   : formset,
            'supplier'  : supplierobj,
        }
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'        
        return render(request, self.template_name, context)

# used to delete a bill object
class PurchaseDeleteView(SuccessMessageMixin, DeleteView):
    model = PurchaseBill
    template_name = "purchases/delete_purchase.html"
    success_url = '/transactions/purchases'
    
    def delete(self, *args, **kwargs):
        self.object = self.get_object()
        items = PurchaseItem.objects.filter(billno=self.object.billno)
        for item in items:
            stock = get_object_or_404(Stock, name=item.stock.name)
            if stock.is_deleted == False:
                stock.quantity -= item.quantity
                stock.save()
        messages.success(self.request, "Purchase bill has been deleted successfully")
        return super(PurchaseDeleteView, self).delete(*args, **kwargs)

# shows the list of bills of all sales 
class SaleView(ListView):
    model = RfqBill
    template_name = "sales/sales_list.html"
    context_object_name = 'bills'
    ordering = ['-created_at']
    paginate_by = 10
    def get(self, request):
        user = self.request.user
        if self.request.user.is_staff:
            supplier = Supplier_details.objects.filter(user=user).first()
            rfq = RfqBill.objects.filter(supplier=supplier)
            print(supplier,rfq)
        else:
            customer = Customer.objects.filter(user=user).first()
            rfq = RfqBill.objects.filter(customer=customer)
            print(customer,rfq)
        context = {'bills':rfq}
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'        
        return render(request, self.template_name, context)

# used to generate a bill object and save items
class SaleCreateView(View):
    template_name = 'sales/new_sale.html'

    def get(self, request):
        form = SaleForm(request.GET or None)
        formset = SaleItemFormset(request.GET or None)                          # renders an empty formset
        stocks = Stock.objects.filter(is_deleted=False)
        context = {
            'form'      : form,
            'formset'   : formset,
            'stocks'    : stocks,
        }
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'        
        return render(request, self.template_name, context)

    def post(self, request):
        form = SaleForm(request.POST)
        formset = SaleItemFormset(request.POST)                                 # recieves a post method for the formset
        if form.is_valid() and formset.is_valid():
            # saves bill
            try:
                billobj = form.save(commit=False)
                billobj.save()

            except Exception as exc:
                print('Exception error! ',exc)
                context = {
                    'form'      : form,
                    'formset'   : formset,
                }
                context['base_template'] = 'customer_base.html'
                if self.request.user.is_staff:
                    context['base_template'] = 'supplier_base.html'                
                return render(request, self.template_name, context)

            try:
                # create bill details object
                billdetailsobj = SaleBillDetails(billno=billobj)
                billdetailsobj.save()

            except Exception as exc:
                print('Exception error! ',exc)
                # Removing purchase transaction to keep transaction data clean
                billobj.delete()
                context = {
                    'form'      : form,
                    'formset'   : formset,
                }
                context['base_template'] = 'customer_base.html'
                if self.request.user.is_staff:
                    context['base_template'] = 'supplier_base.html'                
                return render(request, self.template_name, context)

            for form in formset:                                                # for loop to save each individual form as its own object
                # false saves the item and links bill to the item
                billitem = form.save(commit=False)
                billitem.billno = billobj                                       # links the bill object to the items
                # gets the stock item
                stock = get_object_or_404(Stock, name=billitem.stock.name)
                # calculates the total price
                billitem.totalprice = billitem.perprice * billitem.quantity
                # updates quantity in stock db
                stock.quantity -= billitem.quantity
                billdetailsobj.total += billitem.totalprice
                # saves bill item and stock
                stock.save()
                billitem.save()

            billdetailsobj.save()
            messages.success(request, "Sold items have been registered successfully")
            return redirect('sale-bill', billno=billobj.billno)
        form = SaleForm(request.GET or None)
        formset = SaleItemFormset(request.GET or None)
        context = {
            'form'      : form,
            'formset'   : formset
        }
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'        
        return render(request, self.template_name, context)

# used to delete a bill object
class SaleDeleteView(SuccessMessageMixin, DeleteView):
    model = SaleBill
    template_name = "sales/delete_sale.html"
    success_url = '/transactions/sales'
    
    def delete(self, *args, **kwargs):
        self.object = self.get_object()
        items = SaleItem.objects.filter(billno=self.object.billno)
        for item in items:
            stock = get_object_or_404(Stock, name=item.stock.name)
            if stock.is_deleted == False:
                stock.quantity += item.quantity
                stock.save()
        messages.success(self.request, "Sale bill has been deleted successfully")
        return super(SaleDeleteView, self).delete(*args, **kwargs)

# used to display the purchase bill object
class PurchaseBillView(View):
    model = PurchaseBill
    template_name = "bill/purchase_bill.html"
    bill_base = "bill/bill_base.html"

    def get(self, request, billno):
        context = {
            'bill'          : PurchaseBill.objects.get(billno=billno),
            'items'         : PurchaseItem.objects.filter(billno=billno),
            'billdetails'   : PurchaseBillDetails.objects.get(billno=billno),
            'bill_base'     : self.bill_base,
        }
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'        
        return render(request, self.template_name, context)

    def post(self, request, billno):
        form = PurchaseDetailsForm(request.POST)
        if form.is_valid():
            billdetailsobj = PurchaseBillDetails.objects.get(billno=billno)
            
            billdetailsobj.eway = request.POST.get("eway")    
            billdetailsobj.veh = request.POST.get("veh")
            billdetailsobj.destination = request.POST.get("destination")
            billdetailsobj.po = request.POST.get("po")
            billdetailsobj.cgst = request.POST.get("cgst")
            billdetailsobj.sgst = request.POST.get("sgst")
            billdetailsobj.igst = request.POST.get("igst")
            billdetailsobj.cess = request.POST.get("cess")
            billdetailsobj.tcs = request.POST.get("tcs")
            billdetailsobj.total = request.POST.get("total")

            billdetailsobj.save()
            messages.success(request, "Bill details have been modified successfully")
        context = {
            'bill'          : PurchaseBill.objects.get(billno=billno),
            'items'         : PurchaseItem.objects.filter(billno=billno),
            'billdetails'   : PurchaseBillDetails.objects.get(billno=billno),
            'bill_base'     : self.bill_base,
        }
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'        
        return render(request, self.template_name, context)

# used to display the sale bill object
class SaleBillView(View):
    model = RfqBill
    template_name = "bill/sale_bill.html"
    bill_base = "bill/bill_base.html"
    
    def get(self, request, billno):
        rfq_bill = RfqBill.objects.filter(billno=billno).first()
        demand = Demand.objects.get(pk=rfq_bill.demand.id)
        items = DemandParts.objects.filter(demand =rfq_bill.demand.id)
        quote = Quote.objects.filter(pk = rfq_bill.quote.id).first()
        supplier = Supplier_details.objects.filter(pk = rfq_bill.supplier.id).first()
        customer = Customer.objects.filter(pk = rfq_bill.customer.id).first()
        total = 0
        for each in items:
            total += each.quantity
        total = total*quote.quote_price
        context = {
            'bill'  : rfq_bill,
            'demand' : demand,
            'items' :items,
            'quote': quote,
            'supplier':supplier,
            'customer':customer,
            'total' : total,
        }
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'        
        return render(request, self.template_name, context)

    def post(self, request, billno):
        form = SaleDetailsForm(request.POST)
        if form.is_valid():
            billdetailsobj = SaleBillDetails.objects.get(billno=billno)
            
            billdetailsobj.eway = request.POST.get("eway")    
            billdetailsobj.veh = request.POST.get("veh")
            billdetailsobj.destination = request.POST.get("destination")
            billdetailsobj.po = request.POST.get("po")
            billdetailsobj.cgst = request.POST.get("cgst")
            billdetailsobj.sgst = request.POST.get("sgst")
            billdetailsobj.igst = request.POST.get("igst")
            billdetailsobj.cess = request.POST.get("cess")
            billdetailsobj.tcs = request.POST.get("tcs")
            billdetailsobj.total = request.POST.get("total")

            billdetailsobj.save()
            messages.success(request, "Bill details have been modified successfully")
        context = {
            'bill'          : SaleBill.objects.get(billno=billno),
            'items'         : SaleItem.objects.filter(billno=billno),
            'billdetails'   : SaleBillDetails.objects.get(billno=billno),
            'bill_base'     : self.bill_base,
        }
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'        
        return render(request, self.template_name, context)

class DemandListStatusView(LoginRequiredMixin, ListView):
    model = Demand
    template_name = "demand/demand_list.html"
    paginate_by = 10
    def get_queryset(self):
        status = self.kwargs.get('status')
        user = self.request.user.id
        print(self.request.user.id)
        if self.request.user.is_staff:
            supplier_id = Supplier_details.objects.filter(user=user).first()
            if supplier_id:
                supplier = supplier_id.pk
            else:
                supplier = None
            demand = Demand.objects.filter(is_deleted=False, status=status, supplier_id=supplier)
        else:
            demand = Demand.objects.filter(is_deleted=False, status=status, user=user)
        return demand
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return context    

class DemandListView(LoginRequiredMixin, ListView):
    model = Demand
    template_name = "demand/demand_list.html"
    paginate_by = 10
    def get_queryset(self):
        user = self.request.user
        sort = self.request.GET.get('sort', '')
        industry_id = self.request.GET.get('industry', '')
        if user.is_staff:
            queryset = Demand.objects.filter(end_date__gte=timezone.now(), quote_id=0, is_deleted=False)
        else:
            queryset = Demand.objects.filter(user=user, is_deleted=False)
        if industry_id:
            queryset = queryset.filter(industry_id=industry_id)
        if sort == 'date_asc':
            queryset = queryset.order_by('end_date')
        elif sort == 'date_desc':
            queryset = queryset.order_by('-end_date')
        elif sort == 'parts_asc':
            queryset = queryset.order_by('parts')
        elif sort == 'parts_desc':
            queryset = queryset.order_by('-parts')
        if sort == 'cr_date_asc':
            queryset = queryset.order_by('created_at')
        elif sort == 'cr_date_desc':
            queryset = queryset.order_by('-created_at')
        else:
            queryset = queryset.order_by('-pk')
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sort'] = self.request.GET.get('sort', '')
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return context


class DemandCreateView(SuccessMessageMixin, CreateView):
    model = Demand
    form_class = SelectDemand
    template_name = "demand/edit_demand.html"
    success_url = '/transactions/demand'
    success_message = "RFQ has been created successfully"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'New RFQ'
        context["savebtn"] = 'Add RFQ'
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        PartFormSet = formset_factory(DemandPartsForm, extra=1)
        context["formset"] = PartFormSet()
        return context

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        if form.is_valid():
            # Save the Demand instance
            demand = form.save()

            # Handle the DemandParts forms
            num_parts = int(request.POST.get('parts', 0))
            PartFormSet = formset_factory(DemandPartsForm, extra=num_parts)
            parts_formset = PartFormSet(request.POST, request.FILES)
            if parts_formset.is_valid():
                for part_form in parts_formset:
                    if part_form.cleaned_data:
                        part = part_form.save(commit=False)
                        part.demand = demand
                        part.save()
            return redirect(self.success_url)
        else:
            return self.form_invalid(form)



class DemandUpdateView(SuccessMessageMixin, UpdateView):
    model = Demand
    form_class = SelectDemand
    success_url = '/transactions/demand'
    success_message = "RFQ details has been updated successfully"
    template_name = "demand/edit_demand.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'Edit Demand'
        context["savebtn"] = 'Save Changes'
        context["delbtn"] = 'Delete Demand'
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return context

class DemandDeleteView(View):
    template_name = "demand/delete_demand.html"
    success_message = "Demand Record has been deleted successfully"
    def get(self, request, pk):
        base_template = 'customer_base.html'
        if self.request.user.is_staff:
            base_template = 'supplier_base.html'
        demand = get_object_or_404(Demand, pk=pk)
        return render(request, self.template_name, {'object' : demand,'base_template':base_template})

    def post(self, request, pk):
        demand = get_object_or_404(Demand, pk=pk)
        demand.is_deleted = True
        demand.save()
        messages.success(request, self.success_message)
        return redirect('demand-list')

class DemandView(View):
    def get(self, request, pk):
        demand = get_object_or_404(Demand, pk=pk)
        demanddetails = DemandParts.objects.filter(demand=demand).all()
        quote = Quote.objects.filter(demand=demand)
        btn_class = 'ghost-blue'
        demand.demand_buttons = utils.demand_buttons(demand,request.user.is_staff)
        base_template = 'customer_base.html'
        if self.request.user.is_staff:
            base_template = 'supplier_base.html'
        return render(request, 'demand/demand.html', {'demand' : demand, 'quotes' : quote, 'demanddetails':demanddetails, 'btn_class' : btn_class ,'base_template':base_template})

class QuoteListView(ListView):
    model = Quote
    template_name = "quote/quote_list.html"
    queryset = Quote.objects.filter(is_deleted=False)
    paginate_by = 10

    def get_queryset(self):
        user = self.request.user.id
        supplier = Supplier_details.objects.filter(user=user).first()
        if supplier:
            supplier = supplier.pk
        queryset = Quote.objects.filter(is_deleted=False, supplier = supplier )
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return context    

class QuoteCreateView(SuccessMessageMixin, CreateView):
    model = Quote
    form_class = SelectQuote
    success_url = '/transactions/quote'
    success_message = "Quotation has been created successfully"
    template_name = "quote/edit_quote.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'New Quote'
        context["savebtn"] = 'Add Quote'
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        context["demand"] = Demand.objects.filter(pk=self.kwargs.get('pk')).first()
        return context

    def post(self, request, *args, **kwargs):
        user = request.POST.get('user')
        supplier_id = request.POST.get('supplier')
        quote_price = request.POST.get('quote_price')
        note = request.POST.get('note')
        pk = self.kwargs.get('pk')
        demand = Demand.objects.get(pk=pk)
        supplier_details = Supplier_details.objects.get(user=supplier_id)
        quote = Quote(
            demand=demand,
            supplier=supplier_details,
            quote_price=quote_price,
            note=note
        )
        quote.save()
        messages.success(request, self.success_message)
        return redirect(self.success_url)

class QuoteUpdateView(SuccessMessageMixin, UpdateView):
    model = Quote
    form_class = SelectQuote
    success_url = '/transactions/quote'
    success_message = "Qutation details has been updated successfully"
    template_name = "quote/edit_quote.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = 'Edit Quote'
        context["savebtn"] = 'Save Changes'
        context["delbtn"] = 'Delete Quote'
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return context

class QuoteDeleteView(View):
    template_name = "quote/delete_quote.html"
    success_message = "Quotation has been deleted successfully"
    def get(self, request, pk):
        quote = get_object_or_404(Quote, pk=pk)
        base_template = 'customer_base.html'
        if self.request.user.is_staff:
            base_template = 'supplier_base.html'          
        return render(request, self.template_name, {'object' : quote,'base_template':base_template})

    def post(self, request, pk):
        quote = get_object_or_404(Quote, pk=pk)
        quote.is_deleted = True
        quote.save()
        messages.success(request, self.success_message)
        return redirect('quote-list')

class QuoteView(View):
    def get(self, request, pk):
        quote = get_object_or_404(Quote, pk=pk)
        base_template = 'customer_base.html'
        if self.request.user.is_staff:
            base_template = 'supplier_base.html'          
        return render(request, 'quote/quote.html', {'quote': quote,'base_template':base_template})
 

class QuoteStatusUpdateView(ListView):
    def get(self, request, pk, status):
        quote = get_object_or_404(Quote, pk=pk)
        demand = Demand.objects.get(pk=quote.demand.id)
        btn_class = 'ghost-green'
        if status == 'Approved':
            quote.status = 'Approved'
            demand.status = "Approved"
            demand.quote_id = pk
            demand.supplier_id = quote.supplier_id  # --- WRONGLY SUPPLIER ID Updated
            demand.save()
            reject_others_quotes = Quote.objects.filter(demand=quote.demand.id, status__isnull=True)
            for rejectquote in reject_others_quotes:
                print(rejectquote)
                rejectquote.status = 'Rejected'
                rejectquote.save()
        elif status == 'Rejected':
            quote.status = 'Rejected'
            btn_class = 'ghost-red'
        quote.save()
        demanddetails = DemandParts.objects.filter(demand=demand).all()
        context = {
            'demand': quote.demand,  # Assuming demand is related to Quote
            'quote': quote,
            'btn_class' : btn_class,
            'demanddetails':demanddetails,
        }
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'        
        return redirect(reverse('demand', kwargs={'pk': demand.id}))


class DemandStatusUpdateView(ListView):
    def get(self, request, pk, status):
        demand = get_object_or_404(Demand, pk=pk)
        if status == 'Production' and demand.status == 'Approved':
            demand.status = 'Production'
            demand.save()
        if status == 'Completed' and demand.status == 'Production' :
            demand.status = 'Completed'
            demand.save()
            if not RfqBill.objects.filter(demand = demand):
                quote = get_object_or_404(Quote, pk=demand.quote_id)
                print(quote)
                supplier = get_object_or_404(Supplier_details, pk=demand.supplier_id)
                print(supplier)
                customer = get_object_or_404(Customer, user=demand.user.id)
                print(customer)
                rfq_bill = RfqBill.objects.create(demand = demand, quote = quote, supplier = supplier, customer = customer )
        return redirect(reverse('demand', kwargs={'pk': demand.id}))


class global_search_view(LoginRequiredMixin, ListView):
    model = Demand
    template_name = "globalsearch.html"
    paginate_by = 10
    def get_queryset(self):
        query = self.request.GET.get('search')
        user = self.request.user
        fieldlist = ['user','title','rfq_desc','quote_currency','request_reason','parts','end_date','industry','file']
        split_query = query.split(' ', 1)
        search_field = 'title'
        search_value = ''
        if len(split_query) >= 2:
            search_field = split_query[0]
            if search_field not in fieldlist:
                search_field = 'title'
            search_value = split_query[1:]
        elif len(split_query) == 1:
            search_value = split_query[0]
        if search_value:
            queryset = Demand.objects.filter(is_deleted=False)
            return queryset.filter(**{f"{search_field}__icontains": search_value})
        else:
            queryset = Demand.objects.filter(id = 0, is_deleted=False)
        return queryset
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        context['base_template'] = 'customer_base.html'
        if self.request.user.is_staff:
            context['base_template'] = 'supplier_base.html'
        return context

