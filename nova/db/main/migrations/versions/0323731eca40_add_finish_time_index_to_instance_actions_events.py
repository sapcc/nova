# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy
# of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.

"""add_finish_time_index_to_instance_actions_events

Revision ID: 0323731eca40
Revises: 2903cd72dc14
Create Date: 2026-10-23 15:04:11.820614
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = '0323731eca40'
down_revision = '2903cd72dc14'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('instance_actions_events') as batch_op:
        batch_op.create_index('ix_instance_actions_events_finish_time',
                              ('finish_time',))
