"""Task-local reuse of authenticated packages from the other exact bundle."""
from .common_bundle_repository import CommonBundleRepository
from .dependency_resolution import UnsatisfiedDependency
from .bundle_dependencies import FIRMWARE_OWNED
from .bundle_dependency_rules import requirements


class SharedBundlePackages:
    """Keep primary indexes and roots; borrow only missing exact-profile packages."""
    def __init__(self, primary, profile, device, retain, cancel, tell):
        self.primary = primary
        self.original = profile
        self.device = device
        self.retain = retain
        self.cancel = cancel
        self.tell = tell
        self.other = None
        self.secondary = None
        self.loaded = False
        self.borrowed = set()

    def __getattr__(self, name):
        return getattr(self.primary, name)

    def _load(self):
        if self.loaded:
            return
        if self.cancel is not None and self.cancel.is_set():
            raise InterruptedError('Offline package reuse cancelled')
        self.loaded = True
        kind = 'capture' if self.primary.kind == 'common' else 'common'
        other = self.retain(CommonBundleRepository(kind=kind))
        profile = other.profile(self.device)
        identity = ('release', 'target', 'architecture', 'package_manager')
        if profile is None or any(profile[k] != self.original[k] for k in identity):
            return
        self.other, self.secondary = other, profile

    @staticmethod
    def _shareable(row):
        name = row['name']
        return name not in FIRMWARE_OWNED and not name.startswith('kmod-')

    def package_row(self, profile, name, version):
        row = self.primary.package_row(profile, name, version)
        if row is not None:
            return row
        self._load()
        if self.secondary is not None:
            row = self.other.package_row(self.secondary, name, version)
            if row is not None and self._shareable(row):
                return row
        return None

    def ipk_selection(self, profile, wanted, installed, version_check=None):
        comparisons = {}
        def compare(actual, operator, expected):
            key = (actual, operator, expected)
            if key not in comparisons:
                comparisons[key] = version_check(*key)
            return comparisons[key]
        check = compare if version_check is not None else None
        try:
            return self.primary.ipk_selection(profile, wanted, installed, check)
        except UnsatisfiedDependency:
            self._load()
            if self.secondary is None:
                raise
        rows = {row['name']: row for row in profile['packages']}
        for row in self.secondary['packages']:
            if row['name'] not in rows and self._shareable(row):
                rows[row['name']] = row
        changed = set()
        # The shared resolver limits a dependency closure to 64 packages.
        # Change only the provider of its precise unresolved requirement.
        for _attempt in range(65):
            try:
                return self.primary.ipk_selection(
                    dict(profile, packages=list(rows.values())), wanted, installed, check)
            except UnsatisfiedDependency as error:
                selected = None
                for requirement in requirements(error.expression):
                    if requirement['negative']:
                        continue
                    for row in self.secondary['packages']:
                        name = row['name']
                        if (name in changed or name in installed or name in wanted
                                or rows.get(name) is row or not self._shareable(row)):
                            continue
                        if (requirement['name'] != name
                                and requirement['name'] not in row.get('provides', ())):
                            continue
                        operator = {'<<': '<', '>>': '>'}.get(requirement['operator'], requirement['operator'])
                        if operator and (check is None or not check(row['version'], operator, requirement['version'])):
                            continue
                        selected = row
                        break
                    if selected is not None:
                        break
                if selected is None or len(changed) >= 64:
                    raise
                rows[selected['name']] = selected
                changed.add(selected['name'])
        raise ValueError('Offline dependency reuse exceeds closure limit')

    def materialize(self, row, directory, cancel=None):
        if any(row is entry for entry in self.original['packages']):
            return self.primary.materialize(row, directory, cancel)
        if (self.secondary is None or not self._shareable(row)
                or not any(row is entry for entry in self.secondary['packages'])):
            raise ValueError('Package is outside authenticated reuse profiles')
        if row['name'] not in self.borrowed:
            self.tell('\u4ece\u53e6\u4e00\u4efd\u79bb\u7ebf\u5305\u590d\u7528\u4f9d\u8d56\uff1a' + row['name'] + ' ' + row['version'], 'info')
            self.borrowed.add(row['name'])
        return self.other.materialize(row, directory, cancel)
