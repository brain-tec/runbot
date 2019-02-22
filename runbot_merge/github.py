import collections
import itertools
import json as json_
import logging

import requests

from odoo.tools import topological_sort
from . import exceptions

_logger = logging.getLogger(__name__)
class GH(object):
    def __init__(self, token, repo):
        self._url = 'https://api.github.com'
        self._repo = repo
        session = self._session = requests.Session()
        session.headers['Authorization'] = 'token {}'.format(token)

    def __call__(self, method, path, params=None, json=None, check=True):
        """
        :type check: bool | dict[int:Exception]
        """
        r = self._session.request(
            method,
            '{}/repos/{}/{}'.format(self._url, self._repo, path),
            params=params,
            json=json
        )
        if check:
            if isinstance(check, collections.Mapping):
                exc = check.get(r.status_code)
                if exc:
                    raise exc(r.text)
            if r.status_code >= 400 and r.headers.get('content-type', '').startswith('application/javascript'):
                raise requests.HTTPError(
                    json_.dumps(r.json(), indent=4),
                    response=r
                )
            r.raise_for_status()
        return r

    def user(self, username):
        r = self._session.get("{}/users/{}".format(self._url, username))
        r.raise_for_status()
        return r.json()

    def head(self, branch):
        d = self('get', 'git/refs/heads/{}'.format(branch)).json()

        assert d['ref'] == 'refs/heads/{}'.format(branch)
        assert d['object']['type'] == 'commit'
        _logger.debug("head(%s, %s) -> %s", self._repo, branch, d['object']['sha'])
        return d['object']['sha']

    def commit(self, sha):
        c = self('GET', 'git/commits/{}'.format(sha)).json()
        _logger.debug('commit(%s, %s) -> %s', self._repo, sha, shorten(c['message']))
        return c

    def comment(self, pr, message):
        self('POST', 'issues/{}/comments'.format(pr), json={'body': message})
        _logger.debug('comment(%s, %s, %s)', self._repo, pr, shorten(message))

    def close(self, pr, message):
        self.comment(pr, message)
        self('PATCH', 'pulls/{}'.format(pr), json={'state': 'closed'})

    def change_tags(self, pr, from_, to_):
        to_add, to_remove = to_ - from_, from_ - to_
        for t in to_remove:
            r = self('DELETE', 'issues/{}/labels/{}'.format(pr, t), check=False)
            # successful deletion or attempt to delete a tag which isn't there
            # is fine, otherwise trigger an error
            if r.status_code not in (200, 404):
                r.raise_for_status()

        if to_add:
            self('POST', 'issues/{}/labels'.format(pr), json=list(to_add))

        _logger.debug('change_tags(%s, %s, remove=%s, add=%s)', self._repo, pr, to_remove, to_add)

    def fast_forward(self, branch, sha):
        try:
            self('patch', 'git/refs/heads/{}'.format(branch), json={'sha': sha})
            _logger.debug('fast_forward(%s, %s, %s) -> OK', self._repo, branch, sha)
        except requests.HTTPError:
            _logger.debug('fast_forward(%s, %s, %s) -> ERROR', self._repo, branch, sha, exc_info=True)
            raise exceptions.FastForwardError()

    def set_ref(self, branch, sha):
        # force-update ref
        r = self('patch', 'git/refs/heads/{}'.format(branch), json={
            'sha': sha,
            'force': True,
        }, check=False)

        status0 = r.status_code
        _logger.debug(
            'set_ref(update, %s, %s, %s -> %s (%s)',
            self._repo, branch, sha, status0,
            'OK' if status0 == 200 else r.text or r.reason
        )
        if status0 == 200:
            return

        # 422 makes no sense but that's what github returns, leaving 404 just
        # in case
        status1 = None
        if status0 in (404, 422):
            # fallback: create ref
            r = self('post', 'git/refs', json={
                'ref': 'refs/heads/{}'.format(branch),
                'sha': sha,
            }, check=False)
            status1 = r.status_code
            _logger.debug(
                'set_ref(create, %s, %s, %s) -> %s (%s)',
                self._repo, branch, sha, status1,
                'OK' if status1 == 201 else r.text or r.reason
            )
            if status1 == 201:
                return

        raise AssertionError("set_ref failed(%s, %s)" % (status0, status1))

    def merge(self, sha, dest, message):
        r = self('post', 'merges', json={
            'base': dest,
            'head': sha,
            'commit_message': message,
        }, check={409: exceptions.MergeError})
        try:
            r = r.json()
        except Exception:
            raise exceptions.MergeError("Got non-JSON reponse from github: %s %s (%s)" % (r.status_code, r.reason, r.text))
        _logger.debug("merge(%s, %s, %s) -> %s", self._repo, dest, shorten(message), r['sha'])
        return dict(r['commit'], sha=r['sha'])

    def rebase(self, pr, dest, reset=False, commits=None):
        """ Rebase pr's commits on top of dest, updates dest unless ``reset``
        is set.

        Returns the hash of the rebased head.
        """
        logger = _logger.getChild('rebase')
        original_head = self.head(dest)
        if commits is None:
            commits = self.commits(pr)

        logger.debug("rebasing %s, %s on %s (reset=%s, commits=%s)",
                     self._repo, pr, dest, reset, len(commits))

        assert commits, "can't rebase a PR with no commits"
        for c in commits:
            assert len(c['parents']) == 1, "can't rebase commits with more than one parent"
            tmp_msg = 'temp rebasing PR %s (%s)' % (pr, c['sha'])
            c['new_tree'] = self.merge(c['sha'], dest, tmp_msg)['tree']['sha']

        prev = original_head
        for c in commits:
            copy = self('post', 'git/commits', json={
                'message': c['commit']['message'],
                'tree': c['new_tree'],
                'parents': [prev],
                'author': c['commit']['author'],
                'committer': c['commit']['committer'],
            }, check={409: exceptions.MergeError}).json()
            logger.debug('copied %s to %s (parent: %s)', c['sha'], copy['sha'], prev)
            prev = copy['sha']

        if reset:
            self.set_ref(dest, original_head)
        else:
            self.set_ref(dest, prev)

        logger.debug('rebased %s, %s on %s (reset=%s, commits=%s) -> %s',
                      self._repo, pr, dest, reset, len(commits),
                      prev)
        # prev is updated after each copy so it's the rebased PR head
        return prev

    # fetch various bits of issues / prs to load them
    def pr(self, number):
        return (
            self('get', 'issues/{}'.format(number)).json(),
            self('get', 'pulls/{}'.format(number)).json()
        )

    def comments(self, number):
        for page in itertools.count(1):
            r = self('get', 'issues/{}/comments'.format(number), params={'page': page})
            yield from r.json()
            if not r.links.get('next'):
                return

    def reviews(self, number):
        for page in itertools.count(1):
            r = self('get', 'pulls/{}/reviews'.format(number), params={'page': page})
            yield from r.json()
            if not r.links.get('next'):
                return

    def commits_lazy(self, pr):
        for page in itertools.count(1):
            r = self('get', 'pulls/{}/commits'.format(pr), params={'page': page})
            yield from r.json()
            if not r.links.get('next'):
                return

    def commits(self, pr):
        """ Returns a PR's commits oldest first (that's what GH does &
        is what we want)
        """
        commits = list(self.commits_lazy(pr))
        # map shas to the position the commit *should* have
        idx =  {
            c: i
            for i, c in enumerate(topological_sort({
                c['sha']: [p['sha'] for p in c['parents']]
                for c in commits
            }))
        }
        return sorted(commits, key=lambda c: idx[c['sha']])

    def statuses(self, h):
        r = self('get', 'commits/{}/status'.format(h)).json()
        return [{
            'sha': r['sha'],
            **s,
        } for s in r['statuses']]

def shorten(s):
    if not s:
        return s

    line1 = s.split('\n', 1)[0]
    if len(line1) < 50:
        return line1

    return line1[:47] + '...'
