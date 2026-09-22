# ManufactureHub - The Supply Demand Management System

ManufactureHub is a robust and efficient Supply, Demand and inventory management system built with Django. It allows users to add stock items, generate bills, and manage inventory seamlessly. All data is stored in a database and rendered in real-time, providing a smooth user experience.

## Getting Started

### Prerequisites

Ensure you have Python and Django installed on your machine. If not, you can download and install them from the following links:
- [Python](https://www.python.org/downloads/)
- [Django](https://www.djangoproject.com/download/)

### Installation

1. **Clone the Repository**

   ```bash
   git clone https://github.com/yourusername/ManufactureHub.git
   cd ManufactureHub
   ```

2. **Install Dependencies**

   Create a virtual environment and activate it:

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
   ```

   Install the required packages:

   ```bash
   pip install -r requirements.txt
   ```

### Database Setup

To set up the database for the first time, run the following commands in the project's directory. This process involves creating and applying migrations for each app within the project.

```bash
python manage.py makemigrations core
python manage.py migrate core
python manage.py makemigrations homepage
python manage.py migrate homepage
python manage.py makemigrations accounts
python manage.py migrate accounts
python manage.py makemigrations transactions
python manage.py migrate transactions
python manage.py makemigrations inventory
python manage.py migrate inventory
```

For subsequent model changes, you only need to run:

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py populate_choices
```

### Running the Server

To start the development server, use the following command:

```bash
python manage.py runserver
```

Visit `http://127.0.0.1:8000` in your web browser to access the application.

### Creating an Admin User

To create an admin user for accessing the Django admin interface, run:

```bash
python manage.py createsuperuser
```

Follow the prompts to set the username, email, and password.

## Features

- **Real-time Data Rendering**: All inventory data is updated and rendered in real-time.
- **User Management**: Create and manage users with different levels of access.
- **Stock Management**: Add, update, and remove stock items.
- **Billing**: Generate and manage bills for inventory items.
- **Admin Interface**: Manage the system through a powerful and intuitive admin interface.

## Contributing

We welcome contributions to enhance ManufactureHub. To contribute:

1. Fork the repository.
2. Create a new branch (`git checkout -b feature/your-feature`).
3. Make your changes and commit them (`git commit -am 'Add some feature'`).
4. Push to the branch (`git push origin feature/your-feature`).
5. Open a pull request.

## Licensing and Commercial Terms

ManufactureHub is a commercial software product. Usage of this application is chargeable, and the source code is available under separate commercial terms. For licensing and purchasing details, please contact rajkumarv88@icloud.com.

## Contact

For any questions or feedback, please contact us at [rajkumarv88@icloud.com](mailto:rajkumarv88@icloud.com).

---

With ManufactureHub, managing your supply, demand and inventory has never been easier. Get started today and experience a streamlined inventory management process!