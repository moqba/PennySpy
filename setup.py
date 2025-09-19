#!/usr/bin/env python

from distutils.core import setup

from setuptools import find_packages

setup(
    name='pennyspy',
    description='Scrape transaction history from banks',
    version='0.3.2',
    author="Mohcine Qbaich",
    author_email='randeomcom@gmail.com',
    url='https://github.com/moqba/PennySpy',
    license='MIT',
    packages=find_packages(include=['pennyspy', 'pennyspy.*']),
    install_requires=['requests', 'selenium', 'chromedriver', 'fake-useragent', 'fastapi', 'pydantic', 'uvicorn'],
    extras_require={
        'dev': ['pytest',]
    },
    python_requires='>=3.11',
    entry_points={
        "console_scripts": [
            'pennyspy_api = pennyspy.pennyspy_api:run'
        ]
    }
)