import asyncpg
import asyncio
import os

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "admin")
DB_PASS = os.getenv("DB_PASS", "adminpassword")
DB_NAME = os.getenv("DB_NAME", "aerofleet")

async def init_db():
    print(f"Connecting to database {DB_NAME} at {DB_HOST}:{DB_PORT}...")
    try:
        conn = await asyncpg.connect(user=DB_USER, password=DB_PASS, database=DB_NAME, host=DB_HOST, port=DB_PORT)
    except asyncpg.exceptions.InvalidCatalogNameError:
        print(f"Database {DB_NAME} does not exist. Creating it...")
        sys_conn = await asyncpg.connect(user=DB_USER, password=DB_PASS, database="postgres", host=DB_HOST, port=DB_PORT)
        await sys_conn.execute(f"CREATE DATABASE {DB_NAME}")
        await sys_conn.close()
        conn = await asyncpg.connect(user=DB_USER, password=DB_PASS, database=DB_NAME, host=DB_HOST, port=DB_PORT)

    print("Initializing schema...")
    
    # Enable TimescaleDB extension
    await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")

    await conn.execute("DROP TABLE IF EXISTS alerts CASCADE;")
    await conn.execute("DROP TABLE IF EXISTS telemetry CASCADE;")

    # Telemetry table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS telemetry (
            time TIMESTAMPTZ NOT NULL,
            truck_id VARCHAR(50) NOT NULL,
            model VARCHAR(50),
            speed_kmh FLOAT,
            rpm FLOAT,
            coolant_temp_f FLOAT,
            oil_pressure_psi FLOAT,
            boost_pressure_psi FLOAT,
            fuel_rate_gal_hr FLOAT,
            fuel_pct FLOAT,
            brake_g FLOAT,
            tyre_psi FLOAT,
            lat FLOAT,
            lng FLOAT,
            progress_pct FLOAT
        );
    """)

    # Create hypertable for telemetry if not exists
    await conn.execute("""
        SELECT create_hypertable('telemetry', 'time', if_not_exists => TRUE);
    """)

    # Alerts table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id SERIAL PRIMARY KEY,
            time TIMESTAMPTZ NOT NULL,
            truck_id VARCHAR(50) NOT NULL,
            type VARCHAR(50),
            severity VARCHAR(20),
            description TEXT,
            status VARCHAR(20) DEFAULT 'ACTIVE',
            speed_at_alert FLOAT,
            video_path TEXT
        );
    """)

    # Create hypertable for alerts
    try:
        await conn.execute("""
            SELECT create_hypertable('alerts', 'time', if_not_exists => TRUE);
        """)
    except Exception as e:
        pass # If already a hypertable, it might throw depending on the version if if_not_exists isn't fully supported for alerts primary key
        
    # 1-minute Rollup Continuous Aggregate
    await conn.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS rollup_1m
        WITH (timescaledb.continuous) AS
        SELECT time_bucket('1 minute', time) AS bucket,
               truck_id,
               AVG(speed_kmh) AS avg_speed_kmh,
               MAX(brake_g) AS max_brake_g,
               SUM(fuel_rate_gal_hr) / 60.0 AS est_fuel_gal
        FROM telemetry
        GROUP BY bucket, truck_id;
    """)

    # 1-hour Rollup Continuous Aggregate
    await conn.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS rollup_1h
        WITH (timescaledb.continuous) AS
        SELECT time_bucket('1 hour', time) AS bucket,
               truck_id,
               AVG(speed_kmh) AS avg_speed_kmh,
               MAX(brake_g) AS max_brake_g,
               SUM(fuel_rate_gal_hr) / 60.0 AS est_fuel_gal,
               COUNT(time) FILTER (WHERE brake_g > 0.4) AS harsh_brake_count
        FROM telemetry
        GROUP BY bucket, truck_id;
    """)

    print("Database initialization complete.")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(init_db())
