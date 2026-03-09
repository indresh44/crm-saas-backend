from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.models.customer import Customer, CustomerCreate, CustomerRead

router = APIRouter()


@router.post("/customers", response_model=CustomerRead)
def create_customer(customer: CustomerCreate, session: Session = Depends(get_session)) -> Customer:
    db_customer = Customer.model_validate(customer)
    session.add(db_customer)
    session.commit()
    session.refresh(db_customer)
    return db_customer
