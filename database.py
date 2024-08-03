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


def fetch_unpaid_users():
    """Fetches users who have unpaid dues for the current month."""
    conn = get_database_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        query = """
        SELECT
            users.name,
            users.mobile,
            TRIM(TRAILING ', ' FROM
                GROUP_CONCAT(DISTINCT MONTHNAME(payment_schedule.start_date) ORDER BY MONTH(payment_schedule.start_date))) AS Due_Months
        FROM
            users
            LEFT JOIN payment_schedule ON users.id = payment_schedule.user_id 
            AND MONTH(payment_schedule.start_date) <= MONTH(CURRENT_DATE()) 
            AND YEAR(payment_schedule.start_date) = YEAR(CURRENT_DATE())
            AND payment_schedule.payment_status = 'Due'
        WHERE
            payment_schedule.payment_status = 'Due'
        GROUP BY
            users.id,
            users.name,
            users.mobile;
        """
        
        cursor.execute(query)
        result = cursor.fetchall()
        
    finally:
        cursor.close()
        conn.close()
    
    return result

def fetch_paid_users():
    """Fetches users who have paid for the current month."""
    conn = get_database_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        query = """
        SELECT
            users.name,
            users.mobile,
            payment_schedule.amount
        FROM
            users
            INNER JOIN payment_schedule ON
            users.id = payment_schedule.user_id 
            AND MONTH(payment_schedule.start_date) = MONTH(CURRENT_DATE()) 
            AND YEAR(payment_schedule.start_date) = YEAR(CURRENT_DATE())
        WHERE 
            payment_schedule.payment_status = 'paid'
        """
        
        cursor.execute(query)
        result = cursor.fetchall()
        
    finally:
        cursor.close()
        conn.close()
    
    return result

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

def update_payment(user_id, amount, months, batch_id):
    """Update payment records for a user."""
    conn = get_database_connection()
    cursor = conn.cursor()

    try:
        # Calculate the amount per month
        amount_per_month = amount / months
        print(f"Amount per month: {amount_per_month}")

        # Process each month
        for month in range(months):
            # Calculate the start and end dates for each month
            month_start = datetime.now().date() + relativedelta(months=month)
            month_end = month_start + relativedelta(day=31) - relativedelta(days=1)
            month_name = month_start.strftime('%B')
            year = month_start.year

            print(f"Processing month: {month_name} {year}")

            # Check if a record already exists for this user and month
            cursor.execute("""
                SELECT COUNT(*) FROM payment_schedule
                WHERE user_id = %s
                AND month_name = %s
                AND year = %s
            """, (user_id, month_name, year))
            record_exists = cursor.fetchone()[0]
            print(f"Record exists: {record_exists}")

            if record_exists:
                # Update existing record
                cursor.execute("""
                    UPDATE payment_schedule
                    SET amount = %s, start_date = %s, end_date = %s, batch_id = %s, payment_status = 'Due'
                    WHERE user_id = %s AND month_name = %s AND year = %s
                """, (amount_per_month, month_start, month_end, batch_id, user_id, month_name, year))
                print(f"Updated record for user_id {user_id} for {month_name} {year}")
            else:
                # Insert new record
                cursor.execute("""
                    INSERT INTO payment_schedule (user_id, amount, start_date, end_date, batch_id, month_name, year, payment_status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'Due')
                """, (user_id, amount_per_month, month_start, month_end, batch_id, month_name, year))
                print(f"Inserted new record for user_id {user_id} for {month_name} {year}")

        # Commit the transaction
        conn.commit()

    except mysql.connector.Error as err:
        print(f"Error: {err}")
        conn.rollback()  # Rollback in case of error

    finally:
        cursor.close()
        conn.close()


