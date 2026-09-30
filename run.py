import os
from app.server import create_app
if __name__=='__main__':
    from waitress import serve
    serve(create_app(),host=os.getenv('BIND_HOST','127.0.0.1'),port=int(os.getenv('PORT','8080')),threads=8,max_request_body_size=64*1024*1024)
