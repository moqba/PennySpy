from pathlib import Path

from setuptools import setup, find_packages

setup(
    name='pennyspy',
    description='Scrape transaction history from banks',
    version="0.4.3",
    author="Mohcine Qbaich",
    author_email='randeomcom@gmail.com',
    url='https://github.com/moqba/PennySpy',
    license='MIT',
    packages=find_packages(include=['pennyspy', 'pennyspy.*']),
    install_requires=['requests', 'selenium', 'chromedriver', 'fake-useragent', 'fastapi', 'pydantic', 'uvicorn',
                      'pandas', 'bs4', 'ofxtools'],
    extras_require={
        'dev': ['pytest', ]
    },
    python_requires='>=3.11',
    entry_points={
        "console_scripts": [
            'pennyspy_api = pennyspy.pennyspy_api:run'
        ]
    }
)
