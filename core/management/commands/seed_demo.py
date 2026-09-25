import os
from datetime import timedelta
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import *

class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        password = os.getenv('DEMO_PASSWORD')
        if not password: return self.stdout.write(self.style.WARNING('DEMO_PASSWORD не задан.'))
        types = {n: EmployeeType.objects.get_or_create(name=n)[0] for n in ['Медицинский', 'Административный', 'Технический']}
        deps = {n: Department.objects.get_or_create(name=n)[0] for n in ['Поликлиника','Химиотерапия','Лучевая диагностика','Лаборатория','ОАРИТ']}
        people = [('sysadmin','Системный','Администратор','ADMIN',None,'Административный'),('methodist','Айгуль','Турсунова','METHODIST',None,'Административный'),('head','Елена','Пак','HEAD','Поликлиника','Медицинский')]
        people += [(f'employee{i:02d}', f'Сотрудник{i}', 'Демо', 'EMPLOYEE', list(deps)[(i-1)%5], 'Медицинский') for i in range(1,11)]
        users = {}
        for username, first, last, role, dep, etype in people:
            user, created = User.objects.get_or_create(username=username, defaults={'first_name':first,'last_name':last,'is_staff':role=='ADMIN','is_superuser':role=='ADMIN'})
            if created: user.set_password(password); user.save()
            Profile.objects.update_or_create(user=user, defaults={'role':role,'department':deps.get(dep),'employee_type':types[etype],'position':'Специалист','force_password_change':created})
            users[username] = user
        courses=[]
        for direction_title, course_title in [('Безопасность пациента','Безопасная идентификация пациента'),('Инфекционный контроль','Гигиена рук и профилактика инфекций'),('Лекарственная безопасность','Работа с препаратами высокого риска'),('Клиническая эффективность','Коммуникация в клинической команде')]:
            direction,_ = QualityDirection.objects.get_or_create(title=direction_title, defaults={'internal_code':f'DEMO-{len(courses)+1}','description':'Материалы для повышения качества медицинских услуг.'})
            course,_ = Course.objects.get_or_create(direction=direction,title=course_title, defaults={'description':'Изучите уроки и подтвердите знания итоговым тестом.'})
            Lesson.objects.get_or_create(course=course,title='Учебная памятка',defaults={'kind':'LINK','order':1,'url':'https://www.who.int/teams/integrated-health-services/patient-safety'})
            Lesson.objects.get_or_create(course=course,title='Видеолекция ВОЗ',defaults={'kind':'VIDEO','order':2,'url':'https://www.youtube.com/watch?v=I5-dI74zxPg'})
            test,_=Test.objects.get_or_create(course=course,version=1,defaults={'title':f'Итоговый тест: {course_title}','status':'PUBLISHED','is_current':True})
            if not test.questions.exists():
                for i in range(5): Question.objects.create(test=test,text=f'Вопрос {i+1} по теме «{course_title}»',options=['Верный ответ','Неверный ответ','Недостаточно данных'],correct_index=0,correct_indexes=[0],order=i+1)
            courses.append(course)
        for i in range(1,11):
            course=courses[(i-1)%len(courses)]; test=course.tests.filter(is_current=True).first()
            Assignment.objects.get_or_create(user=users[f'employee{i:02d}'],course=course,defaults={'due_date':timezone.localdate()+timedelta(days=14 if i<8 else -2),'passing_score':80,'attempts_allowed':3,'test_version':test})
        self.stdout.write(self.style.SUCCESS('Созданы демонстрационные данные MedQuality.'))
