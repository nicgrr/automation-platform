"""Add the nullable, listing-specific finish override without changing stock."""
from sqlalchemy import inspect,text
from automation_control.database import SessionLocal

def main():
    with SessionLocal() as session:
        if 'finish' not in {c['name'] for c in inspect(session.bind).get_columns('shop_listings')}:
            session.execute(text('ALTER TABLE shop_listings ADD COLUMN finish VARCHAR(40)'))
            session.commit()
            print('Added listing finish override')

if __name__ == '__main__': main()
