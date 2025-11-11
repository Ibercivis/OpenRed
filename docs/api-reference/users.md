# Users API Reference

User authentication, registration, and profile management endpoints.

## Authentication

OpenRed API uses token-based authentication powered by **dj-rest-auth** and **django-allauth**.

### Authentication Flow

1. **Register** → User receives email verification
2. **Verify Email** → Account activated
3. **Login** → Receive authentication token
4. **Use Token** → Include in `Authorization: Token <your-token>` header

## Endpoints

### Registration

#### `POST /api/auth/registration/`

Register a new user account. Sends email verification link to frontend URL.

**Request Body:**
```json
{
  "username": "scientist123",
  "email": "scientist@example.com",
  "password1": "SecurePass123!",
  "password2": "SecurePass123!"
}
```

**Response (201 Created):**
```json
{
  "detail": "Verification e-mail sent."
}
```

**Email Sent:**
User receives email with link: `https://map.open-red.es/verify-email/{key}/`

**Validation Errors (400):**
```json
{
  "username": ["A user with that username already exists."],
  "email": ["A user is already registered with this e-mail address."],
  "password1": ["This password is too common."]
}
```

---

### Email Verification

#### `POST /api/auth/registration/verify-email/`

Verify user's email address after clicking link from registration email.

**Request Body:**
```json
{
  "key": "abc123xyz789"
}
```

**Response (200 OK):**
```json
{
  "detail": "ok"
}
```

**Errors:**
- `404 Not Found`: Invalid or expired verification key

---

### Login

#### `POST /api/auth/login/`

Authenticate user and receive token.

**Request Body:**
```json
{
  "email": "scientist@example.com",
  "password": "SecurePass123!"
}
```

**Response (200 OK):**
```json
{
  "key": "9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b",
  "user": {
    "pk": 42,
    "username": "scientist123",
    "email": "scientist@example.com",
    "first_name": "Jane",
    "last_name": "Doe"
  }
}
```

**Usage:**
```bash
curl -H "Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b" \
     https://api.open-red.es/api/measurements/
```

**Errors:**
- `400 Bad Request`: Invalid credentials
```json
{
  "non_field_errors": [
    "Unable to log in with provided credentials."
  ]
}
```

---

### Logout

#### `POST /api/auth/logout/`

Invalidate current authentication token.

**Headers:**
```
Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b
```

**Response (200 OK):**
```json
{
  "detail": "Successfully logged out."
}
```

---

### Password Reset Request

#### `POST /api/auth/password/reset/`

Request password reset email with frontend URL link.

**Request Body:**
```json
{
  "email": "scientist@example.com"
}
```

**Response (200 OK):**
```json
{
  "detail": "Password reset e-mail has been sent."
}
```

**Email Sent:**
User receives email with link using **base36 UID encoding** (allauth compatible):
```
https://map.open-red.es/password-reset/{uid36}/{token}/
```

Example: `https://map.open-red.es/password-reset/1a/c3g7kh-f8a2b1c0d9e8f7g6/`

**Note:** Even if email doesn't exist, returns 200 to prevent email enumeration.

---

### Password Reset Confirm

#### `POST /api/auth/password/reset/confirm/`

Set new password after clicking reset link.

**Request Body:**
```json
{
  "uid": "1a",
  "token": "c3g7kh-f8a2b1c0d9e8f7g6",
  "new_password1": "NewSecurePass456!",
  "new_password2": "NewSecurePass456!"
}
```

**Response (200 OK):**
```json
{
  "detail": "Password has been reset with the new password."
}
```

**Errors (400):**
```json
{
  "token": ["Invalid value"],
  "new_password2": ["The two password fields didn't match."]
}
```

---

### Change Password

#### `POST /api/auth/password/change/`

Change password for authenticated user (requires old password).

**Headers:**
```
Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b
```

**Request Body:**
```json
{
  "old_password": "CurrentPass123!",
  "new_password1": "NewSecurePass789!",
  "new_password2": "NewSecurePass789!"
}
```

**Response (200 OK):**
```json
{
  "detail": "New password has been saved."
}
```

**Errors (400):**
```json
{
  "old_password": ["Wrong password."]
}
```

---

### User Profile

#### `GET /api/auth/user/`

Get current authenticated user's profile.

**Headers:**
```
Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b
```

**Response (200 OK):**
```json
{
  "pk": 42,
  "username": "scientist123",
  "email": "scientist@example.com",
  "first_name": "Jane",
  "last_name": "Doe"
}
```

---

#### `PUT /api/auth/user/`

Update user profile.

**Headers:**
```
Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b
```

**Request Body:**
```json
{
  "first_name": "Jane",
  "last_name": "Smith"
}
```

**Response (200 OK):**
```json
{
  "pk": 42,
  "username": "scientist123",
  "email": "scientist@example.com",
  "first_name": "Jane",
  "last_name": "Smith"
}
```

---

### List Users (Admin Only)

#### `GET /api/users/`

List all users (requires admin/staff permissions).

**Headers:**
```
Authorization: Token <admin-token>
```

**Response (200 OK):**
```json
[
  {
    "id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "first_name": "Admin",
    "last_name": "User",
    "is_staff": true
  },
  {
    "id": 42,
    "username": "scientist123",
    "email": "scientist@example.com",
    "first_name": "Jane",
    "last_name": "Smith",
    "is_staff": false
  }
]
```

---

## Frontend Integration Examples

### React Registration Flow

```javascript
// Register user
const register = async (userData) => {
  const response = await fetch('https://api.open-red.es/api/auth/registration/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(userData)
  });
  
  if (response.ok) {
    alert('Check your email to verify your account');
  }
};

// Verify email (from URL: /verify-email/:key)
const verifyEmail = async (key) => {
  const response = await fetch('https://api.open-red.es/api/auth/registration/verify-email/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key })
  });
  
  if (response.ok) {
    alert('Email verified! You can now log in.');
    window.location.href = '/login';
  }
};
```

### React Login & Token Storage

```javascript
const login = async (email, password) => {
  const response = await fetch('https://api.open-red.es/api/auth/login/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password })
  });
  
  if (response.ok) {
    const data = await response.json();
    localStorage.setItem('authToken', data.key);
    localStorage.setItem('user', JSON.stringify(data.user));
    return data;
  }
};

// Make authenticated request
const fetchMeasurements = async () => {
  const token = localStorage.getItem('authToken');
  const response = await fetch('https://api.open-red.es/api/measurements/', {
    headers: {
      'Authorization': `Token ${token}`
    }
  });
  return response.json();
};
```

### React Password Reset Flow

```javascript
// Request password reset
const requestPasswordReset = async (email) => {
  const response = await fetch('https://api.open-red.es/api/auth/password/reset/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email })
  });
  
  if (response.ok) {
    alert('Password reset email sent! Check your inbox.');
  }
};

// Confirm password reset (from URL: /password-reset/:uid/:token)
const confirmPasswordReset = async (uid, token, newPassword) => {
  const response = await fetch('https://api.open-red.es/api/auth/password/reset/confirm/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      uid,
      token,
      new_password1: newPassword,
      new_password2: newPassword
    })
  });
  
  if (response.ok) {
    alert('Password reset successful! You can now log in.');
    window.location.href = '/login';
  }
};
```

## Important Notes

### Email URLs

- **Development:** `http://192.168.1.103:3000`
- **Production:** `https://map.open-red.es`

Email links automatically use the correct frontend URL based on `FRONTEND_URL` environment variable.

### UID Encoding

Password reset uses **base36 UID encoding** (allauth format), not base64. This ensures compatibility with allauth's token validation.

Example UID progression:
- User ID 1 → `"1"`
- User ID 10 → `"a"`
- User ID 35 → `"z"`
- User ID 36 → `"10"`

### Token Security

- Tokens never expire (unless explicitly logged out)
- Store tokens securely (localStorage for web, secure storage for mobile)
- Use HTTPS in production to prevent token interception
- Logout invalidates the token server-side

## Related Documentation

- [Email Setup (AWS SES)](../deployment/email-setup.md)
- [Environment Variables](../deployment/environment-variables.md)
- [Devices API](devices.md)
