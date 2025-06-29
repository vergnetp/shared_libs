import time
import os

print(f"Email worker started for {os.getenv('PROJECT', 'unknown')} project")

while True:
    print("Processing emails...")
    time.sleep(30)