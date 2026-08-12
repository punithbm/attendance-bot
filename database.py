import mysql.connector
from datetime import datetime
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

def get_database_connection():
    """Establishes a connection to the database using environment variables."""
    return mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT')),  # Convert port to integer
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )
    
def fetch_user_details(search_term):
    """Fetch user details based on phone number or name."""
    conn = get_database_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        query = """
        SELECT
            users.name,
            users.mobile,
            users.batch_id,
            COALESCE(MAX(payment_schedule.start_date), 'N/A') AS last_payment_date,
            COUNT(DISTINCT attendance.date) AS days_attended  -- Count distinct attendance dates
        FROM
            users
            LEFT JOIN payment_schedule ON users.id = payment_schedule.user_id AND payment_schedule.payment_status = 'paid'
            LEFT JOIN attendance ON users.id = attendance.user_id
        WHERE
            users.mobile = %s OR users.name = %s
            
        GROUP BY
            users.id
        """

        cursor.execute(query, (search_term, search_term))
        result = cursor.fetchone()

    finally:
        cursor.close()
        conn.close()

    return result    
    

def fetch_unpaid_users(limit=5):
    """Fetches users who have unpaid dues, prioritizing the oldest due month."""
    conn = get_database_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        query = """
        SELECT 
            users.id,
            users.name,
            users.mobile,
            users.batch_id,
            users.last_date_attended,
            MIN(payment_schedule.start_date) AS start_date,
            MIN(payment_schedule.start_date) AS oldest_due_date,
            MONTHNAME(MIN(payment_schedule.start_date)) AS Due_Months
        FROM
            users
            INNER JOIN payment_schedule ON users.id = payment_schedule.user_id 
            AND payment_schedule.payment_status = 'Due'
        WHERE
            payment_schedule.follow_up IS NULL
            OR payment_schedule.follow_up < DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY)
        GROUP BY
            users.id, users.name, users.mobile,users.batch_id
        ORDER BY 
            oldest_due_date ASC
        LIMIT %s;
        """

        cursor.execute(query, (limit,))
        result = cursor.fetchall()

    finally:
        cursor.close()
        conn.close()

    return result

def update_payment_status(user_id, month_name, status):
    """Update payment status for a user."""
    conn = get_database_connection()
    cursor = conn.cursor()
    
    try:
        if status == 'ignore':
            query = """
            UPDATE payment_schedule
            SET payment_status = 'paid', amount = 0
            WHERE user_id = %s AND month = %s AND YEAR(start_date) = YEAR(CURRENT_DATE())
            """
            cursor.execute(query, (user_id, month_name))
        else:
            query = """
            UPDATE payment_schedule
            SET payment_status = %s
            WHERE user_id = %s AND month = %s AND YEAR(start_date) = YEAR(CURRENT_DATE())
            """
            cursor.execute(query, (status, user_id, month_name))
        
        conn.commit()
        return True
    except mysql.connector.Error as err:
        print(f"Error: {err}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()



def update_followup_date(user_id, month_name):
    """Update follow-up date for a user."""
    conn = get_database_connection()
    cursor = conn.cursor()

    try:
        query = """
        UPDATE payment_schedule
        SET follow_up = CURRENT_DATE()
        WHERE user_id = %s AND month = %s AND YEAR(start_date) = YEAR(CURRENT_DATE())
        """
        cursor.execute(query, (user_id, month_name))
        conn.commit()
        return True
    except mysql.connector.Error as err:
        print(f"Error: {err}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()
        
        
def get_batch_id_for_user(user_id):
    # Fetch the batch_id from the database or other source
    conn = get_database_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT batch_id FROM users WHERE id = %s", (user_id,))
    result = cursor.fetchone()
    cursor.close()
    conn.close()
    return result[0] if result else 1  # Default batch_id if none found        
        

def update_pack_payment(user_id, start_month, pack_months, amount_per_month, batch_id):
    conn = get_database_connection()
    cursor = conn.cursor()

    try:
        # Get the oldest due month
        cursor.execute("""
        SELECT MIN(start_date) FROM payment_schedule
        WHERE user_id = %s AND payment_status = 'Due'
        """, (user_id,))
        result = cursor.fetchone()

        if result and result[0]:
            start_date = result[0]
        else:
            # If no due payments, use the provided start_month or current date
            current_date = datetime.now()
            if start_month:
                start_date = datetime.strptime(
                    f"{current_date.year}-{start_month}-01", "%Y-%B-%d")
            else:
                start_date = current_date.replace(day=1)

        # Update or create payment status for the pack months
        for i in range(pack_months):
            month_date = start_date + relativedelta(months=i)
            end_date = month_date + \
                relativedelta(months=1) - relativedelta(days=1)
            month_name = month_date.strftime('%B')

            # Check if a record exists for the month
            cursor.execute("""
            SELECT id, payment_status FROM payment_schedule
            WHERE user_id = %s AND month = %s AND YEAR(start_date) = %s
            """, (user_id, month_name, month_date.year))
            result = cursor.fetchone()

            if result:
                # Update existing record
                if result[1] == 'Due':  # Only update if it's unpaid
                    query = """
                    UPDATE payment_schedule
                    SET amount = %s, end_date = %s, payment_status = 'paid', batch_id = %s
                    WHERE id = %s
                    """
                    params = (amount_per_month, end_date, batch_id, result[0])
                    cursor.execute(query, params)
                else:
                    print(
                        f"Record for {month_name} {month_date.year} is already paid. Skipping.")
            else:
                # Create new record
                query = """
                INSERT INTO payment_schedule (user_id, amount, start_date, end_date, month, payment_status, batch_id)
                VALUES (%s, %s, %s, %s, %s, 'paid', %s)
                """
                params = (user_id, amount_per_month, month_date,
                          end_date, month_name, batch_id)
                cursor.execute(query, params)

        conn.commit()
        return True
    except mysql.connector.Error as err:
        print(f"Error: {err}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()

def ensure_payments_table():
    """Create the lightweight `payments` log table if it doesn't exist.

    This is intentionally separate from `payment_schedule` — it records
    payments as they actually arrive (e.g. via WhatsApp), one row per month
    covered. Idempotent per (user_id, month, year).
    """
    conn = get_database_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NULL,
            student_name VARCHAR(255) NOT NULL,
            batch_id INT NULL,
            month VARCHAR(10) NOT NULL,
            year INT NOT NULL,
            amount DECIMAL(10,2) NULL,
            paid_on DATE NULL,
            mode VARCHAR(50) DEFAULT 'whatsapp',
            note TEXT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_user_month (user_id, month, year),
            INDEX idx_month_year (month, year)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """)
        conn.commit()
        return True
    except mysql.connector.Error as err:
        print(f"Error creating payments table: {err}")
        return False
    finally:
        cursor.close()
        conn.close()


def ensure_subscriptions_table():
    """Per-student subscription state for anniversary billing."""
    conn = get_database_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INT PRIMARY KEY,
            anchor_date DATE NULL,
            extension_days INT NOT NULL DEFAULT 0,
            paid_through DATE NULL,
            note TEXT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """)
        conn.commit()
        return True
    except mysql.connector.Error as err:
        print(f"Error creating subscriptions table: {err}")
        return False
    finally:
        cursor.close()
        conn.close()


def upsert_subscription(user_id, **fields):
    """Insert or partially update a subscription row.
    Accepts anchor_date, extension_days, paid_through, note."""
    allowed = ('anchor_date', 'extension_days', 'paid_through', 'note')
    fields = {k: v for k, v in fields.items() if k in allowed}
    conn = get_database_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT user_id FROM subscriptions WHERE user_id = %s", (user_id,))
        exists = cursor.fetchone()
        if exists:
            if fields:
                sets = ", ".join(f"{k} = %s" for k in fields)
                cursor.execute(
                    f"UPDATE subscriptions SET {sets} WHERE user_id = %s",
                    (*fields.values(), user_id),
                )
        else:
            cols = ["user_id"] + list(fields.keys())
            placeholders = ", ".join(["%s"] * len(cols))
            cursor.execute(
                f"INSERT INTO subscriptions ({', '.join(cols)}) VALUES ({placeholders})",
                (user_id, *fields.values()),
            )
        conn.commit()
        return True
    except mysql.connector.Error as err:
        print(f"Error upserting subscription: {err}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()


def get_subscription(user_id):
    """Return the subscription row for a user, or None."""
    conn = get_database_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM subscriptions WHERE user_id = %s", (user_id,))
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def fetch_active_subscriptions():
    """Return active students joined with their subscription state:
    [{user_id, name, batch_id, anchor_date, extension_days, paid_through}]."""
    conn = get_database_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT u.id AS user_id, u.name, u.batch_id,
                   s.anchor_date, COALESCE(s.extension_days, 0) AS extension_days,
                   s.paid_through
            FROM users u
            LEFT JOIN subscriptions s ON s.user_id = u.id
            WHERE u.status = 'active'
        """)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def seed_subscriptions_for_active(extension_days=14):
    """Create a subscription row for every active student that lacks one,
    applying the given extension (the 2026-06-19 two-week grant)."""
    conn = get_database_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO subscriptions (user_id, extension_days)
            SELECT u.id, %s FROM users u
            WHERE u.status = 'active'
              AND u.id NOT IN (SELECT user_id FROM subscriptions)
        """, (extension_days,))
        conn.commit()
        return cursor.rowcount
    except mysql.connector.Error as err:
        print(f"Error seeding subscriptions: {err}")
        conn.rollback()
        return -1
    finally:
        cursor.close()
        conn.close()


def ensure_zoom_alias_table():
    """Map Zoom display names to a student. One student can have many aliases
    (e.g. joins from two devices). user_id NULL means 'ignore this name'
    (host, junk, unidentifiable device)."""
    conn = get_database_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS zoom_aliases (
            zoom_name VARCHAR(255) NOT NULL,
            zoom_name_key VARCHAR(255) NOT NULL,
            user_id INT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (zoom_name_key),
            INDEX idx_user (user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """)
        conn.commit()
        return True
    except mysql.connector.Error as err:
        print(f"Error creating zoom_aliases table: {err}")
        return False
    finally:
        cursor.close()
        conn.close()


def set_zoom_alias(zoom_name, user_id):
    """Map a Zoom display name to a student id (or None to ignore it)."""
    key = ' '.join((zoom_name or '').strip().lower().split())
    if not key:
        return False
    conn = get_database_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO zoom_aliases (zoom_name, zoom_name_key, user_id)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE zoom_name = VALUES(zoom_name), user_id = VALUES(user_id)
            """,
            (zoom_name.strip(), key, user_id),
        )
        conn.commit()
        return True
    except mysql.connector.Error as err:
        print(f"Error setting zoom alias: {err}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()


def fetch_zoom_alias_map():
    """Return {zoom_name_key: user_id_or_None} for every decided alias.
    Presence of a key means it's been decided; value None means 'ignore'."""
    conn = get_database_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT zoom_name_key, user_id FROM zoom_aliases")
        return {k: uid for (k, uid) in cursor.fetchall()}
    finally:
        cursor.close()
        conn.close()


def find_active_users_by_name(term, limit=8):
    """Return active users whose name contains `term` (case-insensitive)."""
    conn = get_database_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT id, name, mobile, batch_id
            FROM users
            WHERE status = 'active' AND name LIKE %s
            ORDER BY name
            LIMIT %s
            """,
            (f"%{term}%", limit),
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def find_active_user_by_mobile(term):
    """Return active users whose mobile contains the given digits."""
    conn = get_database_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT id, name, mobile, batch_id
            FROM users
            WHERE status = 'active' AND mobile LIKE %s
            ORDER BY name
            LIMIT 8
            """,
            (f"%{term}%",),
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def fetch_all_active_users():
    """Return all active users as [{id, name, batch_id}] for fuzzy matching."""
    conn = get_database_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, name, batch_id FROM users WHERE status = 'active'"
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def record_payment(user_id, student_name, batch_id, months=1,
                   start_year=None, start_month=None, amount=None,
                   mode='whatsapp', note=None):
    """Record a payment covering `months` consecutive months.

    Inserts (or refreshes) one row in `payments` per covered month, starting
    at (start_year, start_month). Defaults to the current month.
    Returns the list of covered month labels (e.g. ["July 2026", ...]).
    """
    now = datetime.now()
    base = datetime(start_year or now.year, start_month or now.month, 1)

    conn = get_database_connection()
    cursor = conn.cursor()
    covered = []
    try:
        for i in range(months):
            d = base + relativedelta(months=i)
            month_name = d.strftime('%B')
            year = d.year

            # Does a row already exist for this user (or raw name) + month?
            if user_id is not None:
                cursor.execute(
                    "SELECT id FROM payments WHERE user_id = %s AND month = %s AND year = %s",
                    (user_id, month_name, year),
                )
            else:
                cursor.execute(
                    "SELECT id FROM payments WHERE user_id IS NULL AND student_name = %s AND month = %s AND year = %s",
                    (student_name, month_name, year),
                )
            existing = cursor.fetchone()

            if existing:
                cursor.execute(
                    """
                    UPDATE payments
                    SET paid_on = CURRENT_DATE(), amount = COALESCE(%s, amount),
                        mode = %s, note = COALESCE(%s, note), batch_id = COALESCE(%s, batch_id)
                    WHERE id = %s
                    """,
                    (amount, mode, note, batch_id, existing[0]),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO payments
                        (user_id, student_name, batch_id, month, year, amount, paid_on, mode, note)
                    VALUES (%s, %s, %s, %s, %s, %s, CURRENT_DATE(), %s, %s)
                    """,
                    (user_id, student_name, batch_id, month_name, year, amount, mode, note),
                )
            covered.append(f"{month_name} {year}")

        conn.commit()
        return covered
    except mysql.connector.Error as err:
        print(f"Error recording payment: {err}")
        conn.rollback()
        return None
    finally:
        cursor.close()
        conn.close()


def fetch_paid_students_for_month(year, month_name):
    """Return payments recorded for a given month as
    [{student_name, user_id, batch_id, name (user's clean name or None)}]."""
    conn = get_database_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT p.student_name, p.user_id, p.batch_id, u.name AS user_name
            FROM payments p
            LEFT JOIN users u ON u.id = p.user_id
            WHERE p.month = %s AND p.year = %s
            """,
            (month_name, year),
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def mark_user_inactive(user_id, month_name):
    """Mark a user as inactive, set current month payment to 0 and mark as paid."""
    conn = get_database_connection()
    cursor = conn.cursor()

    try:
        # Update the current month's payment schedule
        query_payment = """
        UPDATE payment_schedule
        SET amount = 0, payment_status = 'paid'
        WHERE user_id = %s AND month= %s AND YEAR(start_date) = YEAR(CURRENT_DATE())
        """
        cursor.execute(query_payment, (user_id, month_name))

        # Delete future payment schedules
        query_delete_future = """
        DELETE FROM payment_schedule
        WHERE user_id = %s AND start_date > LAST_DAY(CURRENT_DATE())
        """
        cursor.execute(query_delete_future, (user_id,))

        # Update user status to inactive
        query_user = """
        UPDATE users
        SET status = 'inactive'
        WHERE id = %s
        """
        cursor.execute(query_user, (user_id,))

        conn.commit()
        return True
    except mysql.connector.Error as err:
        print(f"Error: {err}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()
