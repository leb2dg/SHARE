from share.normalize import ctx
from share.normalize import tools
from share.normalize.parsers import Parser


class Link(Parser):
    url = tools.RunPython('format_link', ctx)
    type = tools.RunPython('get_link_type', ctx)

    def get_link_type(self, link):
        if 'doi' in link:
            return 'doi'
        if self.config.home_page and self.config.home_page in link:
            return 'provider'
        return 'misc'

    def format_link(self, link):
        link_type = self.get_link_type(link)
        if link_type == 'doi':
            return tools.DOI().execute(link)
        return link


class ThroughLinks(Parser):
    link = tools.Delegate(Link, ctx)


class Organization(Parser):
    name = ctx


class Affiliation(Parser):
    pass


class Person(Parser):

    given_name = tools.ParseName(ctx.name).first
    family_name = tools.ParseName(ctx.name).last
    additional_name = tools.ParseName(ctx.name).middle
    suffix = tools.ParseName(ctx.name).suffix

    affiliations = tools.Map(
        tools.Delegate(Affiliation.using(entity=tools.Delegate(Organization))),
        tools.Try(ctx['arxiv:affiliation'])
    )


class Contributor(Parser):
    order_cited = ctx('index')
    cited_name = ctx.name
    person = tools.Delegate(Person, ctx)


class Tag(Parser):
    name = ctx['@term']


class ThroughTags(Parser):
    tag = tools.Delegate(Tag, ctx)


class Subject(Parser):
    name = ctx


class ThroughSubjects(Parser):
    subject = tools.Delegate(Subject, ctx)


class Preprint(Parser):

    title = ctx.entry.title
    description = ctx.entry.summary
    date_published = tools.ParseDate(ctx.entry.published)
    date_updated = tools.ParseDate(ctx.entry.updated)
    contributors = tools.Map(tools.Delegate(Contributor), ctx.entry.author)

    links = tools.Map(
        tools.Delegate(ThroughLinks),
        tools.Try(ctx.entry['arxiv:doi']),
        ctx.entry.id
    )

    subjects = tools.Map(
        tools.Delegate(ThroughSubjects),
        tools.Subjects(
            tools.RunPython(
                'get_subjects',
                tools.Concat(tools.Try(ctx.entry.category))
            )
        )
    )

    tags = tools.Map(
        tools.Delegate(ThroughTags),
        ctx.entry.category
    )

    class Extra:

        resource_id = ctx.entry.id
        journal_ref = tools.Try(ctx.entry['arxiv:journal_ref'])
        comment = tools.Try(ctx.entry['arxiv:comment'])
        primary_category = tools.Try(ctx.entry['arxiv:primary_category'])

    def get_subjects(self, link):
        return list(map((lambda category: category['@term']), link))
