"""Gunicorn settings for the Docker image, beyond the ones its CMD passes."""


def when_ready(server):
    """Print Cozy's banner once gunicorn is listening."""
    # Imported here, by when gunicorn has put the app's directory on sys.path.
    from cozy.banner import print_banner
    print_banner([str(listener) for listener in server.LISTENERS])
