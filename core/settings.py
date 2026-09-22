import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET_KEY = 'qyu(9l9v%^+r(vt#ecf+36#lis516#3bo5@bo-rd*d%a=!%8#!'
DEBUG = True
ALLOWED_HOSTS = [ '*' ]
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'homepage',
    'accounts',
    'inventory',
    'transactions',
    'widget_tweaks',                            # uses 'django-widget-tweaks' app
    'crispy_forms',                             # uses 'django-crispy-forms' app
    'login_required',                           # uses 'django-login-required-middleware' app
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'transactions.middleware.GlobalSearchMiddleware',
    'login_required.middleware.LoginRequiredMiddleware',    # middleware used for global login
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': ["templates"],  # included 'templates' directory for django to access the html templates
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'invi',
        'USER': 'root',
        'PASSWORD': 'Haripriya!061298',
        'HOST': 'localhost',
        'PORT': '3306',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_L10N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'static')
CRISPY_TEMPLATE_PACK = 'bootstrap4'                     # bootstrap template crispy-form uses
LOGIN_REDIRECT_URL = 'home'                             # sets the login redirect to the 'home' page after login
LOGIN_URL = '/login/#login'                                     # sets the 'login' page as default when user tries to illegally access profile or other hidden pages
LOGIN_REQUIRED_IGNORE_VIEW_NAMES = [                    # urls ignored by the login_required. Can be accessed with out logging in
    'login',
    'logout',
    'about',
    'home',
    'register',
    'register-supplier',
    'register-customer',
]


MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 3600

AUTH_USER_MODEL = 'core.User'
AUTHENTICATION_BACKENDS = [
    'core.backends.EmailBackend',
    'django.contrib.auth.backends.ModelBackend',
]

subscription_plan_details = {
    'basic' : {'price' : '0.00', 'rfq_limit' : '5'},
    'standard' : {'price' : '1500.00', 'rfq_limit' : '10'},
    'enterprise' : {'price' : '3000.00', 'rfq_limit' : 'unlimited'}
}