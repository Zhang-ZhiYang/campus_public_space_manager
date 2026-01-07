# bookings/dao/__init__.py
from .booking_dao import BookingDAO
from .violation_dao import ViolationDAO
# from .penalty_dao import UserPenaltyPointsPerSpaceTypeDAO # 如果你有一个 Penalty DAO

# This dictionary is used by DAOFactoy to find the concrete DAO classes
DAO_CLASSES = {
    'booking': BookingDAO,
    'violation': ViolationDAO,
    # 'penalty': UserPenaltyPointsPerSpaceTypeDAO, # 如果你有
}

# You might also want to expose them directly for easier import elsewhere
__all__ = ['BookingDAO', 'ViolationDAO', 'DAO_CLASSES']