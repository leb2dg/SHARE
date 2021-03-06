import pytest
from urllib3.connection import ConnectionError

from django.apps import apps

from elasticsearch.exceptions import NotFoundError
from elasticsearch.exceptions import ConnectionError as ElasticConnectionError

from share.util import IDObfuscator

from bots.elasticsearch import tasks
from bots.elasticsearch.bot import ElasticSearchBot

from tests import factories


@pytest.fixture
def elastic(settings):
    settings.ELASTICSEARCH_TIMEOUT = 5
    settings.ELASTICSEARCH_INDEX = 'test_' + settings.ELASTICSEARCH_INDEX

    bot = ElasticSearchBot(apps.get_app_config('elasticsearch'), 1, es_setup=False)

    try:
        bot.es_client.indices.delete(index=settings.ELASTICSEARCH_INDEX, ignore=[400, 404])

        bot.setup()
    except (ConnectionError, ElasticConnectionError):
        raise pytest.skip('Elasticsearch unavailable')

    yield bot

    bot.es_client.indices.delete(index=settings.ELASTICSEARCH_INDEX, ignore=[400, 404])


@pytest.mark.django_db
class TestElasticSearchBot:

    @pytest.fixture(autouse=True)
    def elastic(self, elastic):
        return elastic

    def test_index(self, elastic):
        x = factories.AbstractCreativeWorkFactory()
        source = factories.SourceFactory()
        x.sources.add(source.user)

        tasks.IndexModelTask().apply((1, elastic.config.label, 'creativework', [x.id]))

        doc = elastic.es_client.get(index=elastic.es_index, doc_type='creativeworks', id=IDObfuscator.encode(x))

        assert doc['_id'] == IDObfuscator.encode(x)
        assert doc['_source']['title'] == x.title
        assert doc['_source']['sources'] == [source.long_title]

    def test_is_deleted_gets_removed(self, elastic):
        x = factories.AbstractCreativeWorkFactory()
        source = factories.SourceFactory()
        x.sources.add(source.user)

        tasks.IndexModelTask().apply((1, elastic.config.label, 'creativework', [x.id]))
        elastic.es_client.get(index=elastic.es_index, doc_type='creativeworks', id=IDObfuscator.encode(x))

        x.administrative_change(is_deleted=True)

        tasks.IndexModelTask().apply((1, elastic.config.label, 'creativework', [x.id]))

        with pytest.raises(NotFoundError):
            elastic.es_client.get(index=elastic.es_index, doc_type='creativeworks', id=IDObfuscator.encode(x))

    def test_source_soft_deleted(self, elastic):
        x = factories.AbstractCreativeWorkFactory()
        source = factories.SourceFactory(is_deleted=True)
        x.sources.add(source.user)

        tasks.IndexModelTask().apply((1, elastic.config.label, 'creativework', [x.id]))

        doc = elastic.es_client.get(index=elastic.es_index, doc_type='creativeworks', id=IDObfuscator.encode(x))

        assert doc['_id'] == IDObfuscator.encode(x)
        assert doc['_source']['title'] == x.title
        assert doc['_source']['sources'] == []


@pytest.mark.django_db
class TestIndexSource:

    @pytest.fixture(autouse=True)
    def elastic(self, elastic):
        return elastic

    def test_index(self, elastic):
        source = factories.SourceFactory()

        tasks.IndexSourceTask().apply((1, elastic.config.label))

        doc = elastic.es_client.get(index=elastic.es_index, doc_type='sources', id=source.name)

        assert doc['_id'] == source.name
        assert doc['_source']['name'] == source.long_title
        assert doc['_source']['short_name'] == source.name

    def test_index_deleted(self, elastic):
        source = factories.SourceFactory(is_deleted=True)

        tasks.IndexSourceTask().apply((1, elastic.config.label))

        with pytest.raises(NotFoundError):
            elastic.es_client.get(index=elastic.es_index, doc_type='sources', id=source.name)

    def test_index_no_icon(self, elastic):
        source = factories.SourceFactory(icon=None)

        tasks.IndexSourceTask().apply((1, elastic.config.label))

        with pytest.raises(NotFoundError):
            elastic.es_client.get(index=elastic.es_index, doc_type='sources', id=source.name)
