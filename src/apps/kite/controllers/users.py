import requests
from dacite import from_dict

from src.apps.settings.controllers import ConfigController
from ..models import UserModel


class UsersController:
    @staticmethod
    def get_current_user() -> UserModel:
        response = requests.get(
            'https://kite.zerodha.com/oms/user/profile/full',
            headers={
                'Authorization': f'enctoken {ConfigController.get_config().kite_auth_token}'
            }
        )

        if response.status_code != 200:
            raise ValueError('Unexpected response code found: %d, response: %s' % (response.status_code, response.text))

        user_data = response.json()['data']

        return from_dict(data_class=UserModel, data=user_data)
