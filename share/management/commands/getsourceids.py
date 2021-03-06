import yaml

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('work_ids', nargs='*', type=int, help='The id(s) of the creative works to migrate')

    def handle(self, work_ids, *args, **options):

        query = '''
        SELECT DISTINCT acw.id, rd.provider_doc_id, rd.app_label FROM
          share_rawdata AS rd
          JOIN share_normalizeddata AS nd ON rd.id = nd.raw_id
          JOIN share_changeset AS cs ON nd.id = cs.normalized_data_id
          JOIN share_change AS c ON cs.id = c.change_set_id
          JOIN django_content_type AS ct ON c.target_type_id = ct.id
          JOIN share_abstractcreativework AS acw ON c.target_id = acw.id
          WHERE ct.model in ('creativework', 'preprint', 'manuscript', 'publication', 'project', 'registration')
            AND acw.id IN %s AND acw.title != '' AND c.change->>'title' = acw.title
          ORDER BY acw.id;
        '''

        with connection.cursor() as c:
            c.execute(query, (tuple(work_ids),))

            source_ids = {}
            data = c.fetchone()
            while data:
                (id, source_id, app_label) = data
                try:
                    source_ids[id].append([app_label, source_id])
                except KeyError:
                    source_ids[id] = [[app_label, source_id]]
                data = c.fetchone()

        print(yaml.dump(source_ids))
