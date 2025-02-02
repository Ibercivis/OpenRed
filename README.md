# OpenRed
Open Red was created as a platform to collect data from low-cost gamma measurement devices under a citizen science project.

## **📝 Instructions for Development Installation (Using Virtual Environment)**

### **1. Install Dependencies**  
Make sure you have **Python 3.8+** installed on your system.  

On **Debian/Ubuntu**, install the required packages:  
```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
```

On **MacOS** (using Homebrew):  
```bash
brew install python
```

On **Windows**, download and install the latest **Python 3** version from the official website:  
🔗 [Python Downloads](https://www.python.org/downloads/)  

---

### **2. Clone the Repository**  
Download the OpenRed project from GitHub:  
```bash
git clone https://github.com/Ibercivis/OpenRed.git
cd OpenRed
```

---

### **3. Set Up a Virtual Environment**  
Create and activate a virtual environment using `venv`:  

```bash
python3 -m venv venv
source venv/bin/activate  # On macOS/Linux
venv\Scripts\activate  # On Windows (PowerShell)
```

---

### **4. Install Python Dependencies**  
Once the virtual environment is active, install the required dependencies:  

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

### **5. Configure Environment Variables**  
Create a `.env` file inside the **OpenRed** directory:  

```bash
touch .env
```

Edit the `.env` file and add the following:  

```ini
DEBUG=True
SECRET_KEY=your_secret_key
DATABASE_URL=sqlite:///db.sqlite3
ALLOWED_HOSTS=*
```

---

### **6. Apply Migrations & Create Superuser**  
Run the migrations and create an admin user:  

```bash
python manage.py migrate
python manage.py createsuperuser
```

---

### **7. Start the Development Server**  
Run the development server:  

```bash
python manage.py runserver
```

Now you can access the platform at:  
📌 **http://127.0.0.1:8000/**  

---

### **8. Running Tests (Optional, but Recommended)**  
To ensure everything is set up correctly, you can run the tests:  

```bash
python manage.py test
```

---

## **🎯 Next Steps**  
- Modify the code and contribute to OpenRed! 🚀  
- If you need to deactivate the virtual environment, use:  
  ```bash
  deactivate
  ```  
- If you need to restart the database, use:  
  ```bash
  python manage.py flush
  ```

---

This setup uses **SQLite** as the default database, making development simpler. Let me know if you need any modifications! 😊


## Some useful commands
```bash
python ./manage.py add_measures Zaragoza (to add test measures near Zaragoza)
```
