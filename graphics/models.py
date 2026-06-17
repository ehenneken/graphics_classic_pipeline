from sqlalchemy import Column, Integer, String, DateTime, Boolean, create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.dialects import postgresql

try:
    from sqlalchemy.orm import declarative_base
except ImportError:
    from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


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

    def to_dict(self):
        return {
            'id': self.id,
            'bibcode': self.bibcode,
            'doi': self.doi,
            'source': self.source,
            'eprint': self.eprint,
            'figures': self.figures,
            'thumbnails': self.thumbnails,
            'baseurl': self.baseurl,
            'modtime': self.modtime.isoformat() if self.modtime else None,
        }


def get_session(database_url):
    engine = create_engine(database_url)
    Session = scoped_session(sessionmaker(bind=engine))
    return Session()
