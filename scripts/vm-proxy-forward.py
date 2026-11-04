"""ONLY for the overnight build VM: let Docker build containers reach the VM's HTTPS proxy, which listens
on 127.0.0.1 only, through the Docker bridge address. Usage: python3 scripts/vm-proxy-forward.py 172.17.0.1 44337"""
import asyncio
import sys


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def handle(client_reader, client_writer):
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", int(sys.argv[2]))
    except OSError:
        client_writer.close()
        return
    await asyncio.gather(pipe(client_reader, writer), pipe(reader, client_writer))


async def main():
    server = await asyncio.start_server(handle, sys.argv[1], int(sys.argv[2]))
    async with server:
        await server.serve_forever()


asyncio.run(main())
