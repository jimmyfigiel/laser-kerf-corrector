"""WSGI entry point for hosted deployment (e.g. PythonAnywhere). Their "Web"
tab config expects a module-level `application` object -- see README.md's
"Deploying" section for the exact setup steps."""

from kerfcorrector.hub import app as application
