#!/usr/bin/env python

from distutils.core import setup

setup(
    name='pennyspy',
    description='Scrape transaction history from banks',
    version='0.1',
    author="Mohcine Qbaich",
    author_email='randeomcom@gmail.com',
    url='https://github.com/moqba/PennySpy',
    license='MIT',
    packages=['pennyspy'],
    install_requires=['requests', 'selenium', 'chromedriver', 'fake-useragent', 'fastapi', 'pydantic'],
    python_requires='>=3.11'
)