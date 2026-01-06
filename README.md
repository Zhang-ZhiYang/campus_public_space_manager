# campus_public_space_manager
一个灵活的公共空间预订系统，具有特定空间的频率限制和基于违约行为的动态黑名单机制。

好的，这是一个用于介绍你所创建的测试数据结构的 Markdown 文档。你可以将其保存为 `TEST_DATA.md` 并与你的项目一起维护。

---

# 测试数据导入说明 (`import_test_data.py`)

## 概述

`import_test_data.py` 是一个 Django 管理命令，旨在为系统填充一套全面的基础测试数据。这些数据涵盖了用户、角色与权限、空间类型、设施、可预订空间以及预订相关的策略和违规记录，为开发、测试和演示提供了一个功能完备的初始环境。

## 数据导入范围

该脚本导入的数据主要包括以下几个方面：

### 1. 用户与权限体系

*   **用户类型：**
    *   **超级管理员 (admin)：** 拥有系统的最高权限 (`is_superuser`, `is_staff`)。
    *   **系统管理员 (sysadmin)：** 拥有管理后台权限 (`is_staff`)，并属于 `系统管理员` 用户组。
    *   **空间管理员 (spaceman1, spaceman2, spaceman3)：** 拥有管理后台权限 (`is_staff`)，并属于 `空间管理员` 用户组。他们将获得其管理空间的**对象级权限**。
    *   **普通用户 (user1, user2)：** 仅作为普通注册用户。
    *   **受禁用用户 (banneduser)：** 用于测试禁用策略。
*   **用户组：**
    *   `空间管理员`
    *   `系统管理员`
*   **对象级权限：** 通过 `django-guardian` 为空间管理员分配了其所管理空间及其内部设施的特定权限，例如：
    *   `spaces.can_manage_space_details`
    *   `spaces.can_manage_space_bookings`
    *   `spaces.can_manage_space_amenities`
    *   `spaces.can_manage_bookable_amenity`

### 2. 空间与设施基础数据

*   **空间类型 (SpaceType)：** 导入了 6 种空间类型，包括：
    *   `Lecture Hall` (报告厅)
    *   `Meeting Room` (会议室)
    *   `Lab` (实验室，默认需要审批)
    *   `Study Zone` (学习区)
    *   `Sports Field` (运动场)
    *   `Office` (办公室，作为容器空间的类型或基础设施类型)
*   **设施类型 (Amenity)：** 导入了 6 种常见设施，例如：
    *   `Projector` (投影仪)
    *   `Whiteboard` (白板)
    *   `Video Conferencing` (视频会议)
    *   `Water Dispenser` (饮水机)
    *   `Internet Access` (互联网接入)
    *   `Sports Equipment` (体育器材)
*   **空间结构：**
    *   **父级容器空间 (Container Space)：** 3 个作为区域划分，如 `Main Building - Floor 1` (主楼一层), `Innovation Hub` (创新中心), `Outdoor Facilities` (户外设施)。这些空间本身不可直接预订，但用于组织子空间。
    *   **可预订子空间 (Bookable Space)：** 7 个具体可预订空间，分别隶属于上述父级空间和不同的空间类型。例如：`Room 101`, `Lecture Hall A`, `Lab 3B`, `Soccer Field 1` 等。
    *   **空间与管理员关联：** 每个父级和子空间都指定了一个 `managed_by` 空间管理员。
*   **可预订设施实例 (BookableAmenity)：** 为部分可预订子空间配置了具体的设施实例及其数量，例如 `Room 101` 有 1 个 `Projector` 和 1 个 `Whiteboard`。

### 3. 预订管理策略与违规记录

*   **空间类型禁用策略 (SpaceTypeBanPolicy)：** 配置了 3 条禁用策略：
    *   `Meeting Room`：3 违约点，禁用 7 天。
    *   `Lab`：5 违约点，禁用 30 天。
    *   `Global` (全局策略)：10 违约点，禁用 90 天。
*   **用户违约点数 (UserPenaltyPointsPerSpaceType)：** 示例数据，显示用户在特定空间类型下的活跃违约点数。
    *   `user1` 在 `Meeting Room` 有 2 点违约。
    *   `banneduser` 在 `Meeting Room` 有 4 点违约（这将触发禁用）。
*   **用户禁用记录 (UserSpaceTypeBan)：**
    *   `banneduser` 在 `Meeting Room` 被禁用 7 天，由策略触发。
    *   `user2` 被全局手动禁用 90 天。
*   **用户豁免记录 (UserSpaceTypeExemption)：**
    *   `user1` 在 `Lab` 类型下获得为期 30 天的豁免。

## 如何运行

1.  **确保文件结构正确：** 将 `import_test_data.py` 文件放在 `core/management/commands/` 目录下。
2.  **更新用户模型 (如果需要)：** 确保你的 `CustomUser` 模型（通常在 `users/models.py`）按照推荐的方式定义了 `is_system_admin` 和 `is_space_manager` 为 `@property` 属性，并且 `groups` 字段的 `related_name` 设置为 `customuser_set`。
3.  **运行迁移：** 确保数据库结构最新：
    ```bash
    python manage.py makemigrations
    python manage.py migrate
    ```
4.  **执行导入命令：**
    ```bash
    python manage.py import_test_data
    ```

## 默认登录凭据

导入数据后，可以使用以下凭据登录 Django Admin 或进行测试：

| 用户名       | 密码        | 角色          |
| :----------- | :---------- | :------------ |
| `admin`      | `admin123`  | 超级管理员    |
| `sysadmin`   | `sysadmin123` | 系统管理员    |
| `spaceman1`  | `spaceman123` | 空间管理员    |
| `spaceman2`  | `spaceman234` | 空间管理员    |
| `spaceman3`  | `spaceman345` | 空间管理员    |
| `user1`      | `user123`   | 普通用户      |
| `user2`      | `user234`   | 普通用户      |
| `banneduser` | `banned123`  | 受禁用用户    |

## 幂等性

该脚本采用 `get_or_create()` 方法来创建大多数数据。这意味着你可以安全地多次运行该命令，而不会产生重复的数据。如果某个对象已存在，脚本会跳过它的创建并打印警告信息。对于用户，如果已存在，其 `is_staff` 和 `is_superuser` 属性会被更新，而自定义角色（如系统管理员、空间管理员）通过组的分配来确保一致性。

## 使用场景

*   **新项目启动：** 快速搭建一个具备基础数据的开发环境。
*   **功能测试：** 提供预设的用户、空间和策略，方便测试不同的业务逻辑和权限场景。
*   **演示：** 快速展示系统的核心功能和不同用户角色的体验。
*   **CI/CD：** 在自动化测试流程中初始化数据库。

---