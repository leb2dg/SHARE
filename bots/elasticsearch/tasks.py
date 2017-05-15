import logging
import re

import pendulum
import celery

from django.apps import apps
from django.conf import settings
from django.db.models import Min

from elasticsearch import helpers
from elasticsearch import Elasticsearch

from share.models import Agent
from share.models import CreativeWork
from share.models import Subject
from share.models import Tag
from share.models import ShareUser
from share.tasks import AppTask
from share.util import IDObfuscator

from bots.elasticsearch import util


logger = logging.getLogger(__name__)


def safe_substr(value, length=32000):
    if value:
        return str(value)[:length]
    return None


def score_text(text):
    return int(
        (len(re.findall('(?!\d)\w', text)) / (1 + len(re.findall('[\W\d]', text))))
        / (len(text) / 100)
    )


class IndexModelTask(AppTask):

    def do_run(self, model_name, ids, es_url=None, es_index=None):
        errors = []
        model = apps.get_model('share', model_name)
        es_client = Elasticsearch(es_url or settings.ELASTICSEARCH_URL, retry_on_timeout=True, timeout=settings.ELASTICSEARCH_TIMEOUT)

        for ok, resp in helpers.streaming_bulk(es_client, self.bulk_stream(model, ids, es_index or settings.ELASTICSEARCH_INDEX), max_chunk_bytes=32 * 1024 ** 2, raise_on_error=False):
            if not ok:
                logger.warning(resp)
            else:
                logger.debug(resp)

            if not ok and not (resp.get('delete') and resp['delete']['status'] == 404):
                errors.append(resp)

        if errors:
            raise Exception('Failed to index documents {}'.format(errors))

    def bulk_stream(self, model, ids, es_index):
        if not ids:
            return

        opts = {'_index': es_index, '_type': model._meta.verbose_name_plural.replace(' ', '')}

        if model is CreativeWork:
            for blob in util.fetch_creativework(ids):
                if blob.pop('is_deleted'):
                    yield {'_id': blob['id'], '_op_type': 'delete', **opts}
                else:
                    yield {'_id': blob['id'], '_op_type': 'index', **blob, **opts}
            return

        if model is Agent:
            for blob in util.fetch_agent(ids):
                yield {'_id': blob['id'], '_op_type': 'index', **blob, **opts}
            return

        for inst in model.objects.filter(id__in=ids):
            # if inst.is_delete:  # TODO
            #     yield {'_id': inst.pk, '_op_type': 'delete', **opts}
            yield {'_id': inst.pk, '_op_type': 'index', **self.serialize(inst), **opts}

    def serialize(self, inst):
        return {
            Tag: self.serialize_tag,
            Subject: self.serialize_subject,
        }[type(inst)._meta.concrete_model](inst)

    def serialize_tag(self, tag):
        return {
            'id': IDObfuscator.encode(tag),
            'type': 'tag',
            'name': safe_substr(tag.name),
        }

    def serialize_subject(self, subject):
        return {
            'id': IDObfuscator.encode(subject),
            'type': 'subject',
            'name': safe_substr(subject.name),
        }


class IndexSourceTask(AppTask):

    def do_run(self, es_url=None, es_index=None):
        es_client = Elasticsearch(es_url or settings.ELASTICSEARCH_URL, retry_on_timeout=True, timeout=settings.ELASTICSEARCH_TIMEOUT)
        errors = []
        for ok, resp in helpers.streaming_bulk(es_client, self.bulk_stream(es_index or settings.ELASTICSEARCH_INDEX), raise_on_error=False):
            if not ok:
                logger.warning(resp)
            else:
                logger.debug(resp)

            if not ok and not (resp.get('delete') and resp['delete']['status'] == 404):
                errors.append(resp)

        if errors:
            raise Exception('Failed to index documents {}'.format(errors))

    def bulk_stream(self, es_index):
        Source = apps.get_model('share.Source')
        opts = {'_index': es_index, '_type': 'sources'}

        for source in Source.objects.all():
            # remove sources from search that don't appear on the sources page
            if not source.icon or source.is_deleted:
                yield {'_op_type': 'delete', '_id': source.name, **opts}
            else:
                yield {'_op_type': 'index', '_id': source.name, **self.serialize(source), **opts}

    def serialize(self, source):
        return {
            'id': source.name,
            'type': 'source',
            'name': safe_substr(source.long_title),
            'short_name': safe_substr(source.name)
        }


def check_counts_in_range(es_url, es_index, min_date, max_date):
    es_client = Elasticsearch(es_url or settings.ELASTICSEARCH_URL, retry_on_timeout=True, timeout=settings.ELASTICSEARCH_TIMEOUT)
    partial_database_count = CreativeWork.objects.exclude(title='').exclude(is_deleted=True).filter(
        date_created__range=[min_date, max_date]
    ).count()
    partial_es_count = es_client.count(
        index=(es_index or settings.ELASTICSEARCH_INDEX),
        doc_type='creativeworks',
        body={
            'query': {
                'range': {
                    'date_modified': {
                        'gte': min_date,
                        'lte': max_date
                    }
                }
            }
        }
    )['count']
    return (partial_database_count == partial_es_count, partial_database_count, partial_es_count)


def get_date_range_parts(min_date, max_date):
    middle_date = pendulum.parse(min_date).average(pendulum.parse(max_date))
    return {
        'first_half': {'min_date': min_date, 'max_date': middle_date.isoformat()},
        'second_half': {'min_date': middle_date.isoformat(), 'max_date': max_date}
    }


@celery.task(bind=True)
def pseudo_bisection(self, es_url, es_index, min_date, max_date, dry=False):
    '''
    Checks if the database count matches the ES count
    If counts differ, split the date range in half and check counts in smaller ranges
    Pseudo binary because it can't throw away half the results based on the middle value
    '''
    MAX_DB_COUNT = 500
    MIN_MISSING_RATIO = 0.7

    logger.debug(
        'Checking counts for %s to %s',
        pendulum.parse(min_date).format('%B %-d, %Y %I:%M:%S %p'),
        pendulum.parse(max_date).format('%B %-d, %Y %I:%M:%S %p'),
    )

    counts_match, db_count, es_count = check_counts_in_range(es_url, es_index, min_date, max_date)

    if counts_match:
        logger.info(
            'Counts for %s to %s match. %s creativeworks in ES, %s creativeworks in database.',
            pendulum.parse(min_date).format('%B %-d, %Y %I:%M:%S %p'),
            pendulum.parse(max_date).format('%B %-d, %Y %I:%M:%S %p'),
            es_count,
            db_count
        )
        return

    if db_count <= MAX_DB_COUNT or 1 - abs(es_count / db_count) >= MIN_MISSING_RATIO:
        logger.debug('Threshold of %d total works to index or %d%% missing works exceeded', MAX_DB_COUNT, MIN_MISSING_RATIO * 100)

        logger.warning(
            'Counts for %s to %s do not match. %s creativeworks in ES, %s creativeworks in database.',
            pendulum.parse(min_date).format('%B %-d, %Y %I:%M:%S %p'),
            pendulum.parse(max_date).format('%B %-d, %Y %I:%M:%S %p'),
            es_count,
            db_count
        )
        logger.info(
            'Reindexing records created from %s to %s.',
            pendulum.parse(min_date).format('%B %-d, %Y %I:%M:%S %p'),
            pendulum.parse(max_date).format('%B %-d, %Y %I:%M:%S %p')
        )

        if dry:
            logger.debug('dry=True, not reindexing missing works')
        else:
            logger.debug('dry=False, reindexing missing works')
            bot = apps.get_app_config('elasticsearch').get_bot(
                ShareUser.objects.get(username=settings.APPLICATION_USERNAME),
                es_filter={'date_created__range': [min_date, max_date]},
            )
            bot.run()
        return

        logger.debug('Threshold of %d total works to index or %d%% missing works not exceeded', MAX_DB_COUNT, MIN_MISSING_RATIO * 100)

    for key, value in get_date_range_parts(min_date, max_date).items():
        logger.debug('Starting bisection of %s to %s', value['min_date'], value['max_date'])

        if self.request.delivery_info.get('is_eager'):
            pseudo_bisection(es_url, es_index, value['min_date'], value['max_date'], dry=dry)
        else:
            pseudo_bisection.apply_async((es_url, es_index, value['min_date'], value['max_date']), {'dry': dry})


class JanitorTask(AppTask):
    '''
    Looks for discrepancies between postgres and elastic search numbers
    Re-indexes time periods that differ in count
    '''
    def do_run(self, es_url=None, es_index=None, dry=False):
        # get range of date_created in database; assumes current time is the max
        logger.debug('Starting Elasticsearch JanitorTask')

        min_date = CreativeWork.objects.all().aggregate(Min('date_created'))['date_created__min']
        if not min_date:
            logger.warning('No CreativeWorks are present in Postgres. Exiting')
            return

        max_date = pendulum.utcnow()
        min_date = pendulum.instance(min_date)

        pseudo_bisection(es_url, es_index, min_date.isoformat(), max_date.isoformat(), dry=dry)
