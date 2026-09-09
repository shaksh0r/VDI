FROM postgres:16-alpine

COPY ./init.sql /docker-entrypoint-initdb.d/


ENV POSTGRES_DB=VDI
ENV POSTGRES_USER=shakshor
ENV POSTGRES_PASSWORD=shakshor

EXPOSE 5432