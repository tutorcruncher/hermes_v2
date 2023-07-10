from datetime import datetime, timedelta
from hmac import compare_digest
from urllib.parse import urlencode

from fastapi import APIRouter
from starlette.responses import JSONResponse
from tortoise.exceptions import DoesNotExist
from tortoise.expressions import Q

from app.callbooker._availability import get_admin_available_slots
from app.callbooker._booking import check_gcal_open_slots, create_meeting_gcal_event
from app.callbooker._schema import AvailabilityData, CBSalesCall, CBSupportCall
from app.models import Admins, Companies, Contacts, Meetings
from app.settings import Settings
from app.utils import sign_args

cb_router = APIRouter()
settings = Settings()


async def _get_or_create_contact(company: Companies, event: CBSalesCall | CBSupportCall) -> Contacts:
    contact = (
        await Contacts.filter(company_id=company.id)
        .filter(Q(email=event.email) | Q(last_name__iexact=event.last_name))
        .first()
    )
    if not contact:
        contact = await Contacts.create(company_id=company.id, **event.contact_dict())
    return contact


async def _book_call(company: Companies, contact: Contacts, event: CBSalesCall | CBSupportCall):
    # Then we check that the meeting object doesn't already exist for this customer
    if await Meetings.filter(
        contact_id=contact.id,
        start_time__range=(event.meeting_dt - timedelta(hours=2), event.meeting_dt + timedelta(hours=2)),
    ):
        return JSONResponse(
            {'status': 'error', 'message': 'You already have a meeting booked around this time.'}, status_code=400
        )
    # Then we check that the admin has space in their calendar (we query Google for this)
    try:
        admin = await Admins.get(tc_admin_id=event.admin_id)
    except DoesNotExist:
        return JSONResponse({'status': 'error', 'message': 'Admin does not exist.'}, status_code=400)
    meeting_start = event.meeting_dt
    meeting_end = event.meeting_dt + timedelta(minutes=settings.meeting_dur_mins)
    admin_is_free = await check_gcal_open_slots(meeting_start, meeting_end, admin.email)

    if admin_is_free:
        meeting = await Meetings.create(
            company=company,
            contact=contact,
            meeting_type=Meetings.TYPE_SALES if isinstance(event, CBSalesCall) else Meetings.TYPE_SUPPORT,
            start_time=meeting_start,
            end_time=meeting_end,
            admin=admin,
        )
        await create_meeting_gcal_event(meeting=meeting)
        return {'status': 'ok', 'meeting_id': meeting.id}
    else:
        return JSONResponse({'status': 'error', 'message': 'Admin is not free at this time.'}, status_code=400)


async def _get_or_create_contact_company(event: CBSalesCall) -> tuple[Companies, Contacts]:
    """
    Gets or creates a contact and company based on the CBSalesCall data. The logic is a bit complex:
    The company is got by:
    - A submitted cligency_id (if submitted)
    - The contact's email (if they exist) and getting the company from that
    - The name
    The contact is got by:
    - The contact's email (if they exist)
    - Their last name
    If neither objects exist, they are created.
    """
    contact = None
    company = None
    if event.tc_cligency_id:
        company = await Companies.filter(tc_cligency_id=event.tc_cligency_id).first()
    if not company:
        if contact := await Contacts.filter(email=event.email).first():
            company = await contact.company
        else:
            company = await Companies.filter(name__iexact=event.company_name).first()
            if not company:
                company_data = event.company_dict()
                sales_person = await Admins.get(tc_admin_id=event.admin_id)
                company_data['sales_person_id'] = sales_person.id
                company = await Companies.create(**company_data)
    contact = contact or await _get_or_create_contact(company, event)
    return company, contact


@cb_router.post('/sales/book/')
async def sales_call(event: CBSalesCall):
    """
    Endpoint for someone booking a Sales call from the website. Different from the support endpoint as we may need to
    create a new company and contact.
    """
    # TODO: We need to do authorization here

    # First we get or create the company and contact objects.
    company, contact = await _get_or_create_contact_company(event)
    return await _book_call(company, contact, event)


@cb_router.post('/support/book/')
async def support_call(event: CBSupportCall):
    """
    Endpoint for someone booking a Support call from the website. Different from the sales endpoint as we already have
    the company and don't need as much data
    """
    # TODO: We need to do authorization here

    # Get the company and get_or_create the contact
    company = await Companies.get(tc_cligency_id=event.tc_cligency_id)
    contact = await _get_or_create_contact(company, event)
    return await _book_call(company, contact, event)


@cb_router.post('/availability/')
async def availability(avail_data: AvailabilityData):
    """
    Endpoint to return timeslots that an admin is available between 2 datetimes.
    """
    admin = await Admins.get(tc_admin_id=avail_data.admin_id)
    slots = get_admin_available_slots(avail_data.start_dt, avail_data.end_dt, admin)
    return {'status': 'ok', 'slots': slots}


@cb_router.get('/support-link/generate/')
async def generate_support_link(admin_id: int, company_id: int):
    """
    Endpoint to generate a support link for a company from within TC2
    """
    admin = await Admins.get(tc_admin_id=admin_id)
    company = await Companies.get(id=company_id)
    expiry = datetime.now() + timedelta(days=settings.support_ttl_days)
    kwargs = {'admin': admin.id, 'company': company.id, 'e': int(expiry.timestamp())}
    sig = sign_args(**kwargs)
    return {'link': f"{admin.call_booker_url}/?{urlencode({'s': sig, **kwargs})}"}


@cb_router.get('/support-link/validate/')
async def validate_support_link(admin_id: int, company_id: int, expiry: datetime, s: str):
    """
    Endpoint to validate a support link for a company from the website
    """
    kwargs = {'admin': admin_id, 'company': company_id, 'e': expiry}
    sig = sign_args(**kwargs)
    if not compare_digest(sig, s):
        return JSONResponse({'status': 'error', 'message': 'Invalid signature'}, status_code=403)
    elif datetime.now() > expiry:
        return JSONResponse({'status': 'error', 'message': 'Link has expired'}, status_code=403)
    return {'status': 'ok'}
