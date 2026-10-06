from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import exception_handler
from rest_framework.exceptions import ValidationError
import logging

logger = logging.getLogger('my_app')


class SportMatePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response({
            'success': True,
            'count': self.page.paginator.count,
            'next': self.get_next_link(),
            'previous': self.get_previous_link(),
            'results': data,
        })


def sportmate_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        # Log server details only; API consumers always receive a safe JSON error.
        logger.exception('Unhandled API exception', exc_info=exc)
        return Response({'success': False, 'error': {'code': 'SERVER_ERROR', 'message': 'An unexpected server error occurred.', 'details': None}}, status=500)

    details = response.data
    if isinstance(details, dict):
        message = details.get('detail') or 'Request could not be completed.'
    elif isinstance(details, list) and details:
        message = details[0]
    else:
        message = 'Request could not be completed.'

    code_by_status = {
        400: 'VALIDATION_ERROR', 401: 'AUTHENTICATION_REQUIRED',
        403: 'PERMISSION_DENIED', 404: 'NOT_FOUND', 405: 'METHOD_NOT_ALLOWED',
        429: 'RATE_LIMITED',
    }
    error_code = code_by_status.get(response.status_code, 'REQUEST_ERROR')
    default_code = getattr(exc, 'default_code', None)
    if default_code and not isinstance(exc, ValidationError) and str(default_code) != 'error':
        error_code = str(default_code).upper()
    response.data = {
        'success': False,
        'error': {
            'code': error_code,
            'message': str(message),
            'details': details if response.status_code == 400 else None,
        },
    }
    return response
