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
