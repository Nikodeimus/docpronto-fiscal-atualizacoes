from datetime import datetime,timedelta,timezone
# Daily capture follows Brasilia civil time (UTC-3).
BRASILIA=timezone(timedelta(hours=-3))
def first_daily_run(now,hour=8):
    return (datetime.fromtimestamp(now,BRASILIA)+timedelta(days=1)).replace(hour=hour,minute=0,second=0,microsecond=0).timestamp()
def daily_target(now):
    return (datetime.fromtimestamp(now,BRASILIA).date()-timedelta(days=1)).isoformat()
