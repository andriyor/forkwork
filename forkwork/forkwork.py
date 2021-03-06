import calendar
from collections import OrderedDict, namedtuple
from datetime import datetime, timedelta
from email.utils import formatdate, parsedate
from operator import attrgetter
from urllib.parse import urlparse

import cachecontrol
import click
import github3
import pendulum
from cachecontrol.caches import FileCache
from cachecontrol.heuristics import BaseHeuristic
from halo import Halo
from tabulate import tabulate


class OneDayHeuristic(BaseHeuristic):

    def update_headers(self, response):
        date = parsedate(response.headers["date"])
        expires = datetime(*date[:6]) + timedelta(days=1)
        return {
            "expires": formatdate(calendar.timegm(expires.timetuple())),
            "cache-control": "public",
        }

    def warning(self, response):
        msg = "Automatically cached! Response is Stale."
        return "110 - {0}".format(msg)


@click.group()
@click.argument("url")
@click.option("--token", envvar="FORKWORK_TOKEN")
@click.pass_context
def cli(ctx, url, token):
    spinner = Halo(text="Login and fetch forks", spinner="dots")
    spinner.start()

    if token:
        gh = github3.login(token=token)
    else:
        user = click.prompt("username", hide_input=False, confirmation_prompt=False)
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
        gh = github3.login(user, password=password)
    cachecontrol.CacheControl(gh.session, cache=FileCache(".fork_work_cache"), heuristic=OneDayHeuristic())

    login, repo = urlparse(url).path[1:].split("/")
    repository = gh.repository(login, repo)
    forks = repository.forks()

    spinner.stop()
    RepoCtx = namedtuple("Repo", ["repository", "forks", "gh"])
    ctx.obj = RepoCtx(repo, forks, gh)


@cli.command()
@click.pass_obj
def fnm(repo_ctx):
    repo_commits = repo_ctx.repository.commits()
    repo_message = [repo_commit.message for repo_commit in repo_commits]
    old_login = ""
    for fork in repo_ctx.forks:
        # github api may return nonexistent profile
        try:
            for index, commit in enumerate(fork.commits(), 1):
                if commit.message not in repo_message:
                    new_login = fork.owner.login
                    if old_login != fork.owner.login:
                        click.echo("\n", new_login, fork.html_url)
                        old_login = new_login
                    click.echo(index, commit.message, commit.html_url)
        except github3.exceptions.NotFoundError:
            click.echo("Repository {0} not found".format(fork.html_url))


@cli.command()
@click.option("--rows", default=10, help="Numbers of rows")
@click.option("-S", "--star", "sort", flag_value="stargazers_count", default=True, help="Sort by stargazers count")
@click.option("-F", "--forks", "sort", flag_value="forks_count", help="Sort by forks count")
@click.option("-I", "--open_issues", "sort", flag_value="open_issues_count", help="Sort by open issues count")
@click.option("-D", "--updated_at", "sort", flag_value="updated_at", help="Sort by updated at")
@click.option("-P", "--pushed_at", "sort", flag_value="pushed_at", help="Sort by pushed at")
@click.option("-W", "--watchers_count", "sort", flag_value="watchers", help="Sort by watchers count (Slow because "
                                                                            "requires an additional request per fork)")
@click.option("-C", "--commits", "sort", flag_value="commits", help="Sort by number of commits (Slow because requires "
                                                                    "an additional requests per fork)")
@click.option("-B", "--branches", "sort", flag_value="branches", help="Sort by number of branches (Slow because "
                                                                      "requires an additional request per fork)")
@click.pass_obj
def top(repo_ctx, sort, rows):
    repos = []
    columns = OrderedDict(
        [
            ("html_url", "URL"),
            ("stargazers_count", "Stars"),
            ("forks_count", "Forks"),
            ("open_issues_count", "Open Issues"),
            ("updated_at", "Last update"),
            ("pushed_at", "Pushed At"),
        ],
    )
    headers = list(columns.values())

    spinner = Halo(text="Fetch information about forks", spinner="dots")
    spinner.start()

    if sort in {"branches", "commits", "watchers"}:
        columns[sort] = sort.capitalize()
        headers.append(columns[sort])
        Repo = namedtuple("Repo", list(columns.keys()))
    else:
        Repo = namedtuple("Repo", list(columns.keys()))

    for fork in repo_ctx.forks:
        def_prop = [
            fork.html_url,
            fork.stargazers_count,
            fork.forks_count,
            fork.open_issues_count,
            fork.updated_at,
            fork.pushed_at,
        ]
        # github api may return nonexistent profile
        if sort == "branches":
            try:
                def_prop.append(len(list(fork.branches())))
                repos.append(Repo(*def_prop))
            except github3.exceptions.NotFoundError:
                click.echo("\nRepository {0} not found".format(fork.html_url))
        elif sort == "watchers":
            try:
                repo = repo_ctx.gh.repository(fork.owner.login, fork.name)
                def_prop.append(repo.subscribers_count)
                repos.append(Repo(*def_prop))
            except github3.exceptions.NotFoundError:
                click.echo("\nRepository {0} not found".format(fork.html_url))
        elif sort == "commits":
            try:
                def_prop.append(sum((c.contributions_count for c in fork.contributors())))
                repos.append(Repo(*def_prop))
            except github3.exceptions.NotFoundError:
                click.echo("\nRepository {0} not found".format(fork.html_url))
        else:
            repos.append(Repo(*def_prop))

    sorted_forks = sorted(repos, key=attrgetter(sort), reverse=True)
    humanize_dates_forks = []
    for fork in sorted_forks[:rows]:
        days_passed_updated_at = (pendulum.now() - pendulum.parse(fork.updated_at)).days
        days_passed_pushed_at = (pendulum.now() - pendulum.parse(fork.pushed_at)).days
        human_updated_at = pendulum.now().subtract(days=days_passed_updated_at).diff_for_humans()
        human_pushed_at = pendulum.now().subtract(days=days_passed_pushed_at).diff_for_humans()
        humanize_dates_forks.append(fork._replace(updated_at=human_updated_at, pushed_at=human_pushed_at))

    spinner.stop()
    click.echo(tabulate(humanize_dates_forks, headers=headers, tablefmt="grid"))
