import mysql.connector
from datetime import datetime, timedelta
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
            OR payment_schedule.follow_up < DATE_SUB(CURRENT_DATE(), INTERVAL 4 DAY)
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
            WHERE user_id = %s AND month_name = %s AND YEAR(start_date) = YEAR(CURRENT_DATE())
            """
            cursor.execute(query, (user_id, month_name))
        else:
            query = """
            UPDATE payment_schedule
            SET payment_status = %s
            WHERE user_id = %s AND month_name = %s AND YEAR(start_date) = YEAR(CURRENT_DATE())
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
        WHERE user_id = %s AND month_name = %s AND YEAR(start_date) = YEAR(CURRENT_DATE())
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
        # Get the start date of the current month
        cursor.execute("""
        SELECT start_date FROM payment_schedule
        WHERE user_id = %s AND month_name = %s AND YEAR(start_date) = YEAR(CURRENT_DATE())
        """, (user_id, start_month))

        result = cursor.fetchone()
        if result:
            start_date = result[0]
        else:
            print("No start date found for the provided user_id and month.")
            return False
        #print("Result ", result)
        # Update payment status for the pack months
        for i in range(pack_months):
            month_date = start_date + relativedelta(months=i)
            # Adjust end_date to cover the full month
            end_date = (month_date + relativedelta(months=1) - relativedelta(days=1))
            month_name = month_date.strftime('%B')

            # Check if a record already exists for the month
            cursor.execute("""
            SELECT COUNT(*) FROM payment_schedule
            WHERE user_id = %s AND month_name = %s AND YEAR(start_date) = YEAR(CURRENT_DATE())
            """, (user_id, month_name))
            count = cursor.fetchone()[0]

            if count == 0:
                # Insert new record if none exists
                query = """
                INSERT INTO payment_schedule (user_id, amount, start_date, end_date, month_name, payment_status, batch_id)
                VALUES (%s, %s, %s, %s, %s, 'paid', %s)
                """
                params = (user_id, amount_per_month, month_date, end_date, month_name, batch_id)
                cursor.execute(query, params)
            else:
                # Update existing record if it exists
                query = """
                UPDATE payment_schedule
                SET amount = %s, end_date = %s, payment_status = 'paid', batch_id = %s
                WHERE user_id = %s AND month_name = %s AND YEAR(start_date) = YEAR(CURRENT_DATE())
                """
                params = (amount_per_month, end_date, batch_id, user_id, month_name)
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



def mark_user_inactive(user_id, month_name):
    """Mark a user as inactive, set current month payment to 0 and mark as paid."""
    conn = get_database_connection()
    cursor = conn.cursor()

    try:
        # Update the current month's payment schedule
        query_payment = """
        UPDATE payment_schedule
        SET amount = 0, payment_status = 'paid'
        WHERE user_id = %s AND month_name= %s AND YEAR(start_date) = YEAR(CURRENT_DATE())
        """
        cursor.execute(query_payment, (user_id, month_name))

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



def fetch_paid_users():
    """Fetches users who have paid for the current month."""
    conn = get_database_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        query = """
        SELECT
            users.id,
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
        for i in range(months):
            # Calculate the start and end dates for each month
            month_start = datetime.now().date() + relativedelta(months=i)
            month_end = month_start + relativedelta(day=31) - relativedelta(days=1)
            month_name = month_start.strftime('%B')  # Ensure this matches the column type
            year = month_start.year

            print(f"Processing month: {month_name} {year}")

            # Check if a record already exists for this user and month
            cursor.execute("""
                SELECT COUNT(*) FROM payment_schedule
                WHERE user_id = %s AND month = %s
            """, (user_id, month_name))
            record_exists = cursor.fetchone()[0]
            print(f"Record exists: {record_exists}")

            if record_exists:
                # Update existing record
                query = """
                UPDATE payment_schedule
                SET amount = %s, start_date = %s, end_date = %s, batch_id = %s, payment_status = 'Due'
                WHERE user_id = %s AND month = %s
                """
                params = (amount_per_month, month_start, month_end, batch_id, user_id, month_name)
                cursor.execute(query, params)
                print(f"Updated record for user_id {user_id} for {month_name} {year}")
            else:
                # Insert new record
                query = """
                INSERT INTO payment_schedule (user_id, amount, start_date, end_date, batch_id, month, payment_status)
                VALUES (%s, %s, %s, %s, %s, %s, 'Due')
                """
                params = (user_id, amount_per_month, month_start, month_end, batch_id, month_name)
                cursor.execute(query, params)
                print(f"Inserted new record for user_id {user_id} for {month_name} {year}")

        # Commit the transaction
        conn.commit()

    except mysql.connector.Error as err:
        print(f"Error: {err}")
        conn.rollback()  # Rollback in case of error

    finally:
        cursor.close()
        conn.close()
