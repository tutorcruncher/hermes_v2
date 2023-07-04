from typing import Optional

from pydantic import BaseModel, validator, ValidationError


class TCSubject(BaseModel):
    model: str
    id: int

    class Config:
        extra = 'allow'


class _TCSimpleUser(BaseModel):
    """
    Used to parse a role that's used a SimpleRoleSerializer
    """

    id: int
    first_name: str
    last_name: str
    email: str


class _TCAdmin(_TCSimpleUser):
    pass


class _TCAgency(BaseModel):
    id: int
    name: str
    country: str
    website: str
    status: str
    paid_invoice_count: int

    @validator('country')
    def country_to_code(cls, v):
        return v.split(' ').strip('()')

    def dict(self, *args, **kwargs):
        data = super().dict(*args, **kwargs)
        data['tc_agency_id'] = data.pop('id')
        return data

    class Config:
        extra = 'allow'


class TCRecipient(_TCSimpleUser):
    pass


class ClientDeletedError(ValidationError):
    pass


class TCClient(BaseModel):
    id: int
    meta_agency: _TCAgency
    status: str
    associated_admin: Optional[_TCAdmin]
    sales_person: Optional[_TCAdmin]
    bdr_person: Optional[_TCAdmin]
    paid_recipients: list[TCRecipient]

    @validator('meta_agency')
    def meta_agency_exists(cls, v):
        """
        If the client has been deleted in TC then the meta_agency will be not be present.
        """
        if not v:
            raise ClientDeletedError
        return v

    def dict(self, *args, **kwargs):
        return dict(
            tc_agency_id=self.meta_agency.id,
            tc_cligency_id=self.id,
            status=self.meta_agency.status,
            name=self.meta_agency.name,
            country=self.meta_agency.country,
            client_manager=self.associated_admin and self.associated_admin.id,
            sales_person=self.sales_person and self.sales_person.id,
            bdr_person=self.bdr_person and self.bdr_person.id,
        )

    class Config:
        extra = 'allow'


class TCInvoice(BaseModel):
    id: int
    accounting_id: str
    client: _TCSimpleUser

    class Config:
        extra = 'allow'


class TCEvent(BaseModel):
    action: str
    verb: str
    subject: TCSubject

    class Config:
        extra = 'allow'


class TCWebhook(BaseModel):
    events: list[TCEvent]
    _request_time: int
