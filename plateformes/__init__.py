from .aljt import AdaptateurALJT
from .locannonces import AdaptateurLocAnnonces
from .action_logement import AdaptateurActionLogement
from .alin import AdaptateurAlin

ADAPTATEURS = {
    "aljt": AdaptateurALJT,
    "locannonces": AdaptateurLocAnnonces,
    "action_logement": AdaptateurActionLogement,
    "alin": AdaptateurAlin,
}
