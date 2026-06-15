import json
import simplejson
from sqlalchemy import Column, Integer, String, DateTime, Boolean, create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.dialects import postgresql

try:
    from sqlalchemy.orm import declarative_base
    from sqlalchemy.orm.decl_api import DeclarativeMeta
except ImportError:
    from sqlalchemy.ext.declarative import declarative_base, DeclarativeMeta

Base = declarative_base()


class AlchemyEncoder(simplejson.JSONEncoder):

    def default(self, obj):
        if isinstance(obj.__class__, DeclarativeMeta):
            fields = {}
            for field in [x for x in dir(obj)
                          if not x.startswith('_') and x != 'metadata']:
                data = obj.__getattribute__(field)
                try:
                    json.dumps(data)
                    fields[field] = data
                except TypeError:
                    fields[field] = None
            return fields
        return simplejson.JSONEncoder.default(self, obj)


class GraphicsModel(Base):
    __tablename__ = 'graphics'
    id = Column(Integer, primary_key=True)
    bibcode = Column(String, nullable=False, index=True)
    doi = Column(String)
    source = Column(String)
    eprint = Column(Boolean)
    figures = Column(postgresql.JSON)
    thumbnails = Column(postgresql.ARRAY(String), default=[])
    baseurl = Column(String)
    modtime = Column(DateTime)


def get_session(database_url):
    engine = create_engine(database_url)
    Session = scoped_session(sessionmaker(bind=engine))
    return Session()


def create_all_tables(database_url):
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)