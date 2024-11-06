import setuptools
import codecs

test_deps = ['pytest']

try:
    import unittest.mock
except:
    test_deps.append('mock')

setuptools.setup(
    name='doublethink',
    version='0.4.9',
    packages=['doublethink'],
    classifiers=[
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
    ],
    install_requires=['rethinkdb==2.4.9'],
    extras_require={'test': test_deps},
    url='https://github.com/internetarchive/doublethink',
    author='Noah Levitt',
    author_email='nlevitt@archive.org',
    description='rethinkdb python library',
    long_description=codecs.open(
        'README.rst', mode='r', encoding='utf-8').read(),
    license='license.txt',
    entry_points={
            'console_scripts': [
                'doublethink-purge-stale-services=doublethink.cli:purge_stale_services',
            ]
    },
)
