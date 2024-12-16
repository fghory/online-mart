GATEWAY SERVICES
    - Host : host.internal.docker
    - Port: Service Port (e.g. 8000, 8001, 8002 etc)
    - Path: Empty
ROUTES
    - Paths: Path Names to be stripped, like /inventory-service, /product-service etc
    - Strip Path: True
    - Above settings will allow, for e.g., http://127.0.0.1:9000/inventory-service/inventory to be mapped - to http://127.0.0.1:9000/inventory.
        > /inventory-service path will be stripped as it is a matching path. 
        > It will ensure that inventory endpoint is hit through kong gateway.    