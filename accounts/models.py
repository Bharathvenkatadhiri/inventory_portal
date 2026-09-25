# models.py
from django.db import models
#from django.contrib.auth.models import User
from django.conf import settings


class ManufacturingTech(models.Model):
    TECH_CHOICES = [
        ('milling', 'Milling'),
        ('5_axis_milling', '5-axis milling machines'),
        ('form_milling', 'Form milling (3D)'),
        ('full_range_milling', 'Full-range milling (including turning)'),
        ('hsc_milling', 'HSC milling'),
        ('deep_hole_drilling', 'Deep-hole drilling'),
        ('ultrasonic_milling', 'Ultrasonic milling'),
        ('engraver_milling', 'Engraver milling'),
        ('turning', 'Turning'),
        ('full_range_turning', 'Full-range turning'),
        ('anodizing', 'Anodizing'),
        ('cnc_turning', 'CNC turning'),
        ('cmm_inspection', 'CMM inspection'),
        ('sheet_metal', 'Sheet metal fabrication'),
        ('injection_molding', 'Injection molding'),
        ('anodizing_partner', 'Anodizing (via partner)'),
    ]
    technology_type = models.CharField(
        max_length=50,
        choices=TECH_CHOICES,
        default='milling',
    )

    def __str__(self):
        return dict(self.TECH_CHOICES).get(self.technology_type, 'Other Technology')


class MaterialCapability(models.Model):
    MATERIAL_CHOICES = [
        ('structural_steel', 'Structural steel'),
        ('stainless_steel', 'Stainless steel'),
        ('aluminium', 'Aluminium'),
        ('case_hardening', 'Case hardening'),
        ('titanium', 'Titanium'),
        ('brass', 'Brass'),
        ('copper', 'Copper'),
        ('plastics', 'Plastics / polymers'),
    ]
    material_type = models.CharField(max_length=50, choices=MATERIAL_CHOICES, unique=True)

    def __str__(self):
        return dict(self.MATERIAL_CHOICES).get(self.material_type, 'Other Material')


class Company(models.Model):
    """
    A GST-verified business entity. Kept separate from ManufacturerProfile
    (which holds the supplier's marketplace-facing details) so the same
    verified-identity record can later be reused by other roles (e.g. a
    buyer's company) without duplicating GST verification.

    No continuous GST monitoring for the MVP — `gst_verified_at` is a
    point-in-time snapshot from the last verify/refresh call, not a live
    status. Historical documents (e.g. future invoices) must copy the
    fields they need at creation time rather than referencing this row,
    since this row can change on a manual re-verify.
    """

    ENTITY_TYPE_CHOICES = [
        ('private_limited', 'Private Limited'),
        ('public_limited', 'Public Limited'),
        ('llp', 'LLP'),
        ('partnership', 'Partnership'),
        ('proprietorship', 'Proprietorship'),
        ('other', 'Other'),
    ]

    GST_STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('CANCELLED', 'Cancelled'),
        ('SUSPENDED', 'Suspended'),
        ('INACTIVE', 'Inactive'),
        ('UNKNOWN', 'Unknown'),
    ]

    VERIFICATION_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('verified', 'Verified'),
        ('failed', 'Failed'),
        ('manual_review', 'Manual Review'),
    ]

    legal_name = models.CharField(max_length=255)
    trade_name = models.CharField(max_length=255, blank=True)
    gstin = models.CharField(max_length=15, unique=True, null=True, blank=True)
    gst_status = models.CharField(max_length=20, choices=GST_STATUS_CHOICES, blank=True)
    gst_verified = models.BooleanField(default=False)
    gst_verified_at = models.DateTimeField(null=True, blank=True)
    registered_address = models.TextField(blank=True)
    state = models.CharField(max_length=50, blank=True)
    city = models.CharField(max_length=100, blank=True)
    pincode = models.CharField(max_length=10, blank=True)
    # Not every Indian business entity has a CIN (e.g. proprietorships,
    # partnerships) — kept nullable and out of the mandatory flow.
    cin = models.CharField(max_length=21, unique=True, null=True, blank=True)
    mca_status = models.CharField(max_length=30, null=True, blank=True)
    entity_type = models.CharField(max_length=30, choices=ENTITY_TYPE_CHOICES, blank=True)
    verification_status = models.CharField(max_length=20, choices=VERIFICATION_STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['gstin']),
            models.Index(fields=['verification_status']),
        ]

    def __str__(self):
        return self.legal_name or self.trade_name or f"Company #{self.pk}"


class ManufacturerProfile(models.Model):
    EMPLOYEES_CHOICES = [
        ('10-20', '10 - 20'),
        ('20-50', '20 - 50'),
        ('50-100', '50 - 100'),
        ('100-200', '100 - 200'),
        ('200-500', '200 - 500'),
        ('500-1000', '500 - 1000'),
        ('>1000', '> 1000')

    ]

    TURNOVER_CHOICES = [
        ('<1', '< 1 Mil.'),
        ('1-5', '1 - 5 Mil.'),
        ('5-10', '5 - 10 Mil.'),
        ('10-20', '10 - 20 Mil.'),
        ('20-50', '20 - 50 Mil.'),
        ('50-100', '50 - 100 Mil.'),
        ('>100', '> 100 Mil.')
    ]

    CERTIFICATES_CHOICES = [
        ('', 'Not certified'),
        ('ISO-TS 16949:2009', 'ISO-TS 16949:2009'),
        ('ISO 9001:2015', 'ISO 9001:2015'),
        ('ISO 9001:2008', 'ISO 9001:2008'),
        ('ISO 14001', 'ISO 14001'),
        ('OHSAS 18000', 'OHSAS 18000'),
        ('SA 8000', 'SA 8000'),
        ('DIN EN 1090', 'DIN EN 1090'),
        ('ISO-TS 16949:2002', 'ISO-TS 16949:2002'),
        ('IATF 16949:2016', 'IATF 16949:2016'),
        ('VDA 6.1', 'VDA 6.1'),
        ('VDA 6.2', 'VDA 6.2'),
        ('VDA 6.4', 'VDA 6.4'),
        ('EN ISO 13485', 'EN ISO 13485'),
        ('JAR 145', 'JAR 145'),
        ('QSF-A', 'QSF-A'),
        ('QSF-B', 'QSF-B'),
        ('PART 145', 'PART 145'),
        ('DIN 18800-7:2008 Class A-C', 'DIN 18800-7:2008 Class A-C (minor welding cert.)'),
        ('DIN 18800-7:2008 Class D/E', 'DIN 18800-7:2008 Class D/E (major welding cert.)'),
        ('DIN EN ISO 3834', 'DIN EN ISO 3834'),
        ('EN ISO 9001', 'EN ISO 9001'),
        ('EN ISO 14001', 'EN ISO 14001'),
    ]

    ACTIVITY_TYPE_CHOICES = [
        ('1', 'Owner, company manager, member of the board'),
        ('2', 'Technical management'),
        ('3', 'Commercial management'),
        ('4', 'Research and Development'),
        ('12', 'Design'),
        ('16', 'Production'),
        ('22', 'Quality assurance'),
        ('30', 'IT'),
        ('41', 'Purchasing / Material management'),
        ('42', 'Marketing and Sales'),
        ('46', 'Others'),
    ]

    MANUFACTURING_COMPETENCY_CHOICES = [
        ('31', 'Turning'),
        ('34', 'Milling'),
        ('82', 'Drilling / Sawing'),
        ('14', 'Machining (Extruded Profiles)'),
        ('28', 'Sheet Metal Processing'),
        ('25', 'Sheet Metal Forming (Stamping, Deep-drawing)'),
        ('55', 'Welding'),
        ('12', 'Welded constructions / Structural Steelwork'),
        ('13', 'Injection Moulding, Extruding'),
        ('19', 'Joining (Plastics & Rubber)'),
        ('86', 'Cutting (plastics)'),
        ('81', 'Forming (Plastics & Rubber)'),
        ('85', 'Machining (Plastics & Rubber)'),
        ('88', 'Water jet cutting'),
        ('4', 'Casting'),
        ('7', 'Sintering & Powder Pressing'),
        ('5', 'Cutting dies / deep drawing dies'),
        ('8', 'Mould construction'),
        ('9', 'Jigmaking'),
        ('83', 'Etching and Spark Erosion'),
        ('89', 'Cutting tools (metal)'),
        ('90', 'Mechanical Engineering'),
        ('84', 'Assembly Group Manufacturing'),
        ('54', 'Assembly'),
        ('64', 'Measuring & Testing'),
        ('52', 'Gear Making'),
        ('49', 'Threading'),
        ('58', 'Surface Treatment'),
        ('61', 'Heat Treatment'),
        ('91', 'Labelling / marking'),
        ('22', 'Solid Forming'),
        ('46', 'Wire & Spring Bending'),
        ('43', 'Pipe & Tube Manufacturing'),
        ('37', 'Finishing'),
        ('10', 'Additive Manufacturing'),
        ('11', 'Cable Preparation'),
        ('87', 'Tubes and pipes (assembled)'),
    ]

    INFO_SOURCE_CHOICES = [
        ('87', 'Personal Recommendation'),
        ('94', 'Google'),
        ('89', 'Link on Other Web Site'),
        ('294', 'Social Media'),
        ('91', 'Press Release'),
        ('92', 'Trade Fair / Technical Speech'),
        ('96', 'Other'),
    ]

    PAYMENT_LEAD_TIME_HELP = "Typical days from order confirmation to dispatch"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    company = models.OneToOneField(Company, on_delete=models.PROTECT, null=True, blank=True, related_name='manufacturer_profile')
    companyname = models.CharField(max_length=40, blank=True, null=True)
    phone = models.CharField(max_length=12, unique=True)
    address = models.CharField(max_length=200)
    city = models.CharField(max_length=50, blank=False, null=False)
    state = models.CharField(max_length=25, blank=False, null=False)
    country = models.CharField(max_length=25, blank=False, null=False)
    activity_type = models.CharField(max_length=2, choices=ACTIVITY_TYPE_CHOICES, blank=True, null=True)
    company_street = models.CharField(max_length=40, blank=True, null=True)
    company_postalcode = models.CharField(max_length=40, blank=True, null=True)
    company_city = models.CharField(max_length=40, blank=True, null=True)
    company_url = models.URLField(max_length=200, blank=True, null=True)
    production_area = models.PositiveIntegerField(blank=True, null=True)
    manufacturing_competency1 = models.CharField(max_length=2, choices=MANUFACTURING_COMPETENCY_CHOICES, blank=True, null=True)
    manufacturing_competency2 = models.CharField(max_length=2, choices=MANUFACTURING_COMPETENCY_CHOICES, blank=True, null=True)
    info_source = models.CharField(max_length=3, choices=INFO_SOURCE_CHOICES, blank=True, null=True)
    amount_of_employees = models.CharField(max_length=20, choices=EMPLOYEES_CHOICES)
    turnover_per_year = models.CharField(max_length=20, choices=TURNOVER_CHOICES)
    certificates = models.CharField(max_length=40, choices=CERTIFICATES_CHOICES, blank=True, null=True)
    email = models.EmailField(max_length=254, unique=True, blank=False, null=False)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # --- Company profile dashboard (screen 5) ---------------------------
    capabilities = models.ManyToManyField(ManufacturingTech, blank=True, related_name='manufacturers')
    materials = models.ManyToManyField(MaterialCapability, blank=True, related_name='manufacturers')
    about = models.TextField(blank=True)
    cover_image = models.ImageField(upload_to='manufacturer/covers/', blank=True, null=True)
    minimum_order_qty = models.PositiveIntegerField(blank=True, null=True)
    typical_lead_time_days = models.PositiveIntegerField(blank=True, null=True, help_text=PAYMENT_LEAD_TIME_HELP)
    accepting_rfqs = models.BooleanField(default=True)
    contact_name = models.CharField(max_length=100, blank=True)
    contact_role = models.CharField(max_length=100, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return f"{self.user} - CompanyDetails({self.amount_of_employees}, {self.turnover_per_year}, {self.certificates})"

    def profile_strength(self):
        """Real, weighted completeness score for the company profile page —
        never fabricated. Weights sum to 100; each entry doubles as a
        template-driven checklist row with an action link for anything
        still missing."""
        checklist = [
            {
                'label': 'Capabilities and machines',
                'done': self.capabilities.exists() and self.machines.exists(),
                'weight': 20,
                'cta_label': 'Add capabilities',
                'cta_url_name': 'company-profile',
            },
            {
                'label': 'Materials',
                'done': self.materials.exists(),
                'weight': 10,
                'cta_label': 'Add materials',
                'cta_url_name': 'company-profile',
            },
            {
                'label': 'Certification on file',
                'done': self.certifications.exists(),
                'weight': 15,
                'cta_label': 'Upload certificate',
                'cta_url_name': 'company-profile',
            },
            {
                'label': 'About your company',
                'done': bool(self.about.strip()),
                'weight': 10,
                'cta_label': 'Write about',
                'cta_url_name': 'company-profile',
            },
            {
                'label': 'Shop-floor photos',
                'done': self.photos.exists(),
                'weight': 15,
                'cta_label': 'Add shop-floor photos',
                'cta_url_name': 'company-profile',
            },
            {
                'label': 'Primary contact',
                'done': bool(self.contact_name and self.contact_phone),
                'weight': 15,
                'cta_label': 'Add contact',
                'cta_url_name': 'company-profile',
            },
            {
                'label': 'Capacity settings',
                'done': self.minimum_order_qty is not None and self.typical_lead_time_days is not None,
                'weight': 15,
                'cta_label': 'Set capacity',
                'cta_url_name': 'company-profile',
            },
        ]
        percent = sum(item['weight'] for item in checklist if item['done'])
        return percent, checklist


class Machine(models.Model):
    manufacturer = models.ForeignKey(ManufacturerProfile, on_delete=models.CASCADE, related_name='machines')
    machine_type = models.CharField(max_length=100)
    make_model = models.CharField(max_length=100, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    work_envelope = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.machine_type} ({self.manufacturer_id})"


class ManufacturerPhoto(models.Model):
    manufacturer = models.ForeignKey(ManufacturerProfile, on_delete=models.CASCADE, related_name='photos')
    image = models.ImageField(upload_to='manufacturer/photos/')
    caption = models.CharField(max_length=150, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.caption or f"Photo #{self.pk}"


class Certification(models.Model):
    manufacturer = models.ForeignKey(ManufacturerProfile, on_delete=models.CASCADE, related_name='certifications')
    name = models.CharField(max_length=100)
    valid_until = models.DateField(blank=True, null=True)
    document = models.FileField(upload_to='manufacturer/certifications/', blank=True, null=True)
    # Manually flipped by staff for now — no automated certificate
    # verification in scope yet, same point-in-time-manual-flag pattern as
    # Company.verification_status.
    is_verified = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.manufacturer_id})"


# Contains customers
class ConsumerProfile(models.Model):
    BUSINESS_TYPES = [
        ('electronics', 'Electronics'),
        ('infrastructure', 'Infrastructure'),
        ('custom_machinery', 'Custom Machinery'),
        ('Others', 'Others'),
    ]
    INDUSTRY_CHOICES = [
        (75, 'Aerospace and aviation industry'),
        (73, 'Air conditioning, refrigeration and ventilation industry'),
        (56, 'Apparatus engineering'),
        (58, 'Automation and control engineering'),
        (59, 'Automotive and vehicle construction'),
        (74, 'Boiler, container and tank construction'),
        (60, 'Building, agricultural and forestry machinery manufacturing'),
        (65, 'Chemical industry'),
        (81, 'Clean room technology'),
        (61, 'Construction and architectural supplies'),
        (55, 'Drive and gear engineering'),
        (67, 'Electrical industry'),
        (57, 'Fittings engineering'),
        (79, 'Furniture industry'),
        (70, 'Household appliance industry'),
        (71, 'Hydraulic and pneumatic industry'),
        (72, 'Information technology (hardware)'),
        (62, 'Lighting industry'),
        (86, 'Machine tool manufacturing'),
        (77, 'Measurement and control technique, laboratory equipment'),
        (161, 'Mechanical engineering'),
        (76, 'Medical technology'),
        (78, 'Military engineering'),
        (63, 'Mining and tunnel engineering'),
        (64, 'Office machinery and supplies'),
        (85, 'Packaging industry'),
        (80, 'Paper and printing machinery industry'),
        (54, 'Plant engineering and construction'),
        (68, 'Power generation and transmission industry'),
        (69, 'Precision engineering, mechatronics and optics'),
        (66, 'Railway and rail vehicles industry'),
        (82, 'Shipbuilding industry'),
        (83, 'Special purpose machinery manufacturing'),
        (84, 'Telecommunication industry'),
    ]
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    Name = models.CharField(max_length=75, blank=False, null=False)
    type_of_business = models.CharField(max_length=50, choices=BUSINESS_TYPES)
    Address = models.CharField(max_length=150, blank=True, null=True)
    city = models.CharField(max_length=50, blank=False, null=False)
    state = models.CharField(max_length=25, blank=False, null=False)
    country = models.CharField(max_length=25, blank=False, null=False)
    phone = models.CharField(max_length=12, unique=True)
    email = models.EmailField(max_length=254, unique=True)
    EORI_number = models.CharField(max_length=50, unique=True)
    VAT_number = models.CharField(max_length=50, unique=True)
    industry_Choice = models.IntegerField(choices=INDUSTRY_CHOICES, default=0)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"#{self.id} - {self.Name}"

class SubscriptionPlan(models.Model):
    PLAN_CHOICES = [
        ('basic', 'Basic'),
        ('standard', 'Standard'),
        ('enterprise', 'Enterprise'),
    ]
    user_profile = models.ForeignKey(settings.AUTH_USER_MODEL, to_field='email', on_delete=models.CASCADE)
    plan_type = models.CharField(max_length=20, choices=PLAN_CHOICES)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    rfq_limit = models.CharField(max_length=50) # unlimited for Enterprise

    # Additional fields for subscription management
    start_date = models.DateField(auto_now=True)
    end_date = models.DateField(blank=True, null=True) # Set this for expiration
    is_active = models.BooleanField(default=True)

    # A paid upgrade the user asked for but hasn't paid for yet. There is no
    # payment integration, so staff apply it (from the subscriptions admin
    # screen) once payment is confirmed; plan_type/price/rfq_limit only
    # change then.
    pending_plan_type = models.CharField(max_length=20, choices=PLAN_CHOICES, blank=True)
    pending_requested_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.user_profile.username} - {self.plan_type}"
